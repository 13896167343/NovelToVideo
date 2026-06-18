"""语音合成服务"""
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.models.text_segment import TextSegment
from app.models.task import Task
from app.models.project import Project
from app.utils.logger import get_logger
from app.utils.file_handler import FileHandler
from config import DefaultConfig

logger = get_logger(__name__)


class TTSService:
    """语音合成服务类"""
    
    # 线程池
    _executor = ThreadPoolExecutor(max_workers=DefaultConfig.MAX_THREAD_COUNT)
    
    @staticmethod
    def synthesize_project(project_id):
        """
        合成项目的所有语音
        
        Args:
            project_id: 项目ID
            
        Returns:
            (成功标志, 错误信息)
        """
        task_id = None
        try:
            # 创建任务
            task_id = Task.create(project_id, Task.TYPE_AUDIO_SYNTHESIS)
            Task.update_status(task_id, Task.STATUS_RUNNING)
            
            # 更新项目状态
            Project.update_status(project_id, Project.STATUS_PROCESSING)
            
            # 获取项目配置（容错：项目/配置可能为 None）
            project = Project.get_by_id(project_id)
            if not project:
                error_msg = '项目不存在，无法进行语音合成'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                Project.update_status(project_id, Project.STATUS_FAILED)
                return False, error_msg
            config = project.config if isinstance(project.config, dict) else {}
            
            # 重置之前意外中断的 synthesizing 状态段落为 pending，避免遗漏
            TextSegment.reset_audio_status_by_project(
                project_id,
                TextSegment.AUDIO_STATUS_SYNTHESIZING,
                TextSegment.AUDIO_STATUS_PENDING
            )
            
            # 获取待处理的段落
            segments = TextSegment.get_pending_segments(project_id)
            total_segments = len(segments)

            # 确保临时目录存在
            temp_audio_dir = os.path.join(DefaultConfig.TEMP_AUDIO_DIR, str(project_id))
            FileHandler.ensure_dir(temp_audio_dir)

            if total_segments == 0:
                logger.info(f'没有待合成的段落: 项目ID={project_id}')
                # 仍执行音频完整性校验，清理历史遗留的空文件并重置对应段落
                broken_count, broken_ids = TTSService.verify_audio_integrity(project_id)
                if broken_count > 0:
                    logger.warning(f'检测到 {broken_count} 个历史遗留的损坏音频，自动重新合成')
                    resynth_ok, resynth_fail = TTSService._resynthesize_segments(
                        project_id, broken_ids, config, temp_audio_dir
                    )
                    if resynth_fail > 0:
                        error_msg = f'损坏音频重新合成失败: 成功{resynth_ok}, 失败{resynth_fail}'
                        Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                        Project.update_status(project_id, Project.STATUS_FAILED)
                        return False, error_msg
                    logger.info(f'损坏音频重新合成完成: 成功{resynth_ok}')
                # 无段落可处理，任务直接完成，并将项目状态更新为已完成，避免页面长时间处于"处理中"
                Task.update_status(task_id, Task.STATUS_COMPLETED)
                Project.update_status(project_id, Project.STATUS_COMPLETED)
                return True, None

            logger.info(f'开始语音合成: 项目ID={project_id}, 段落数={total_segments}')
            
            # 多线程处理
            completed_count = 0
            failed_count = 0
            
            for segment in segments:
                try:
                    TTSService._synthesize_segment(segment, config, temp_audio_dir)
                    completed_count += 1
                except Exception as e:
                    logger.error(f'段落语音合成失败: segment_id={segment.id}, error={str(e)}')
                    failed_count += 1
                    # 确保段落状态被更新为失败
                    try:
                        TextSegment.update_audio_status(segment.id, TextSegment.AUDIO_STATUS_FAILED)
                    except Exception as status_error:
                        logger.error(f'更新段落状态失败: segment_id={segment.id}, error={str(status_error)}')
                finally:
                    # 确保无论如何都更新进度
                    # 更新进度
                    progress = (completed_count + failed_count) / total_segments * 100
                    Task.update_progress(task_id, progress)
            
            # 完成任务
            if failed_count == 0:
                # 音频完整性校验：清理空文件并将损坏段落重置为 pending，
                # 避免 ffmpeg concat demuxer 在视频合成时遇错中断整次合并
                broken_count, broken_ids = TTSService.verify_audio_integrity(project_id)
                if broken_count > 0:
                    logger.warning(f'检测到 {broken_count} 个音频文件缺失或为空，自动重新合成')
                    resynth_ok, resynth_fail = TTSService._resynthesize_segments(
                        project_id, broken_ids, config, temp_audio_dir
                    )
                    if resynth_fail > 0:
                        error_msg = f'音频完整性校验后重新合成仍失败: 成功{resynth_ok}, 失败{resynth_fail}'
                        Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                        Project.update_status(project_id, Project.STATUS_FAILED)
                        return False, error_msg
                    # 重新合成成功后再校验一次，确保无残留问题
                    broken_count2, _ = TTSService.verify_audio_integrity(project_id)
                    if broken_count2 > 0:
                        error_msg = f'重新合成后仍有 {broken_count2} 个音频文件损坏'
                        Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                        Project.update_status(project_id, Project.STATUS_FAILED)
                        return False, error_msg
                    logger.info(f'损坏音频重新合成完成: 成功{resynth_ok}')

                Task.update_status(task_id, Task.STATUS_COMPLETED)
                logger.info(f'语音合成完成: 项目ID={project_id}')
                return True, None
            else:
                error_msg = f'部分段落合成失败: 成功{completed_count}, 失败{failed_count}'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                # 更新项目状态为失败
                Project.update_status(project_id, Project.STATUS_FAILED)
                return False, error_msg
                
        except Exception as e:
            logger.error(f'语音合成失败: {str(e)}', exc_info=True)
            if task_id:
                Task.update_status(task_id, Task.STATUS_FAILED, str(e))
            # 更新项目状态为失败
            Project.update_status(project_id, Project.STATUS_FAILED)
            return False, str(e)
    
    @staticmethod
    def _synthesize_segment(segment, config, temp_audio_dir):
        """
        合成单个段落的语音
        
        Args:
            segment: 文本段落对象
            config: 配置字典
            temp_audio_dir: 临时音频目录
        """
        # 更新状态为合成中
        TextSegment.update_audio_status(segment.id, TextSegment.AUDIO_STATUS_SYNTHESIZING)
        
        # 音频文件路径
        audio_filename = f'segment_{segment.id}.mp3'
        audio_path = os.path.join(temp_audio_dir, audio_filename)
        
        # 获取语音参数
        voice = config.get('voice', DefaultConfig.DEFAULT_VOICE)
        rate = config.get('rate', DefaultConfig.DEFAULT_RATE)
        pitch = config.get('pitch', DefaultConfig.DEFAULT_PITCH)
        volume = config.get('volume', DefaultConfig.DEFAULT_VOLUME)
        
        # 文本内容防御性处理：空文本直接标记失败，避免 TTS 抛错
        safe_text = (segment.content or '').strip()
        if not safe_text:
            TextSegment.update_audio_status(segment.id, TextSegment.AUDIO_STATUS_FAILED)
            raise Exception('文本内容为空，无法合成语音')
        
        # 根据分段后的文本长度计算超时时间（每1000个字符增加300秒超时，最少300秒）
        # 考虑到edge-tts的分段已经是按照4096字节限制处理过的，这里使用更合理的超时计算
        text_length = len(safe_text)
        timeout_seconds = max(300.0, text_length / 1000 * 300.0)
        logger.info(f'开始合成语音: segment_id={segment.id}, 超时时间={timeout_seconds}秒')

        # 调用 Edge TTS 合成语音
        retry_count = 0
        max_retries = DefaultConfig.TTS_RETRY_COUNT
        
        while retry_count < max_retries:
            try:
                # 使用异步方式调用 edge-tts
                # 使用更安全的方式处理事件循环，避免Event loop closed错误
                try:
                    # 尝试获取当前事件循环
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        # 如果事件循环已关闭，创建新的事件循环
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                except RuntimeError:
                    # 如果没有事件循环，创建新的事件循环
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                loop.run_until_complete(TTSService._async_synthesize(
                    safe_text,
                    voice,
                    rate,
                    pitch,
                    volume,
                    audio_path,
                    timeout_seconds  # 传递计算好的超时时间
                ))
                
                # 验证文件是否生成
                if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                    # 使用 ffprobe 读取音频时长
                    try:
                        import subprocess
                        result = subprocess.run(
                            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
                             '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                            capture_output=True, text=True, encoding='utf-8'
                        )
                        duration = float(result.stdout.strip())
                        logger.info(f'音频时长: {duration:.2f}秒')
                    except Exception as e:
                        logger.warning(f'读取音频时长失败: {str(e)}')
                        duration = None
                    
                    # 更新状态为已完成，并保存时长
                    TextSegment.update_audio_status(
                        segment.id,
                        TextSegment.AUDIO_STATUS_COMPLETED,
                        audio_path,
                        audio_duration=duration
                    )
                    logger.info(f'段落语音合成成功: segment_id={segment.id}')
                    return
                else:
                    raise Exception('音频文件生成失败')
                    
            except Exception as e:
                retry_count += 1
                logger.warning(f'语音合成失败 (尝试 {retry_count}/{max_retries}): {str(e)}')
                
                if retry_count >= max_retries:
                    # 更新状态为失败
                    TextSegment.update_audio_status(segment.id, TextSegment.AUDIO_STATUS_FAILED)
                    raise Exception(f'语音合成失败,已重试{max_retries}次: {str(e)}')
    
    @staticmethod
    async def _async_synthesize(text, voice, rate, pitch, volume, output_path, timeout_seconds=None):
        """
        异步合成语音
        
        Args:
            text: 文本内容
            voice: 语音角色
            rate: 语速
            pitch: 音调
            volume: 音量
            output_path: 输出路径
            timeout_seconds: 超时时间（秒），如果为None则使用默认计算
        """
        try:
            import edge_tts
            
            # 创建通讯对象
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=rate,
                pitch=pitch,
                volume=volume
            )
            
            # 如果没有传递超时时间，则根据文本长度计算超时时间（每1000个字符增加300秒超时，最少300秒）
            # 这里使用更宽松的超时设置，因为异步操作可能需要更多时间
            if timeout_seconds is None:
                text_length = len(text)
                timeout_seconds = max(300.0, text_length / 1000 * 300.0)
            
            # 尝试保存音频文件，设置动态超时时间
            await asyncio.wait_for(communicate.save(output_path), timeout=timeout_seconds)
            
        except asyncio.TimeoutError:
            logger.error('Edge TTS 调用超时')
            raise Exception('语音合成超时，请检查网络连接')
        except Exception as e:
            # 记录详细的错误信息
            logger.error(f'Edge TTS 调用失败: {str(e)}')
            
            # 如果是网络相关错误，提供更详细的错误信息
            error_msg = str(e).lower()
            if '403' in error_msg or 'invalid response status' in error_msg or 'connection' in error_msg:
                logger.warning('检测到网络访问问题，请检查：')
                logger.warning('1. 确保网络连接正常')
                logger.warning('2. 检查防火墙或代理设置')
                logger.warning('3. 确认可以访问 speech.platform.bing.com')

            raise

    @staticmethod
    def verify_audio_integrity(project_id):
        """
        验证项目所有已完成音频文件是否真实存在且非空

        历史上发生过 DB 标记 completed 但文件被中断/丢失的情况（如 segment_115.mp3 = 0 字节），
        若不处理，后续 ffmpeg concat demuxer 会因此类文件中断整次合并。

        对于缺失或为空的音频文件：
        1. 删除该空文件（若存在）
        2. 将数据库 audio_status 重置为 pending，以便重新合成

        Args:
            project_id: 项目ID

        Returns:
            (损坏数量, 损坏段落ID列表)
        """
        try:
            segments = TextSegment.get_completed_segments(project_id)
            if not segments:
                return 0, []

            broken_ids = []
            for segment in segments:
                audio_path = segment.get_absolute_audio_path()
                # 文件缺失
                if not os.path.exists(audio_path):
                    logger.warning(
                        f'音频完整性校验：文件缺失, segment_id={segment.id}, path={audio_path}'
                    )
                    TextSegment.update_audio_status(
                        segment.id, TextSegment.AUDIO_STATUS_PENDING
                    )
                    broken_ids.append(segment.id)
                    continue
                # 文件为空
                try:
                    if os.path.getsize(audio_path) == 0:
                        logger.warning(
                            f'音频完整性校验：文件为空, segment_id={segment.id}, path={audio_path}'
                        )
                        try:
                            os.remove(audio_path)
                            logger.info(f'已删除空音频文件: {audio_path}')
                        except OSError as e:
                            logger.warning(f'删除空音频文件失败: {audio_path}, {e}')
                        TextSegment.update_audio_status(
                            segment.id, TextSegment.AUDIO_STATUS_PENDING
                        )
                        broken_ids.append(segment.id)
                except OSError as e:
                    logger.warning(
                        f'音频完整性校验：读取文件失败, segment_id={segment.id}, path={audio_path}, {e}'
                    )
                    TextSegment.update_audio_status(
                        segment.id, TextSegment.AUDIO_STATUS_PENDING
                    )
                    broken_ids.append(segment.id)

            if broken_ids:
                logger.warning(
                    f'音频完整性校验完成: project_id={project_id}, 共发现 {len(broken_ids)} 个损坏文件, '
                    f'已重置为 pending, ids={broken_ids}'
                )
            return len(broken_ids), broken_ids
        except Exception as e:
            logger.error(f'音频完整性校验异常: project_id={project_id}, {e}', exc_info=True)
            return 0, []

    @staticmethod
    def _resynthesize_segments(project_id, segment_ids, config, temp_audio_dir):
        """
        重新合成指定的段落（用于音频完整性校验后的自动恢复）。

        Args:
            project_id: 项目ID
            segment_ids: 段落ID列表
            config: 项目配置
            temp_audio_dir: 临时音频目录

        Returns:
            (成功数量, 失败数量)
        """
        completed = 0
        failed = 0
        for seg_id in segment_ids:
            seg = TextSegment.get_by_id(seg_id)
            if seg is None:
                logger.warning(f'重新合成时未找到段落: segment_id={seg_id}')
                continue
            try:
                TTSService._synthesize_segment(seg, config, temp_audio_dir)
                completed += 1
            except Exception as e:
                logger.error(f'重新合成失败: segment_id={seg_id}, {e}')
                failed += 1
        logger.info(f'损坏音频重新合成结果: 成功={completed}, 失败={failed}')
        return completed, failed
