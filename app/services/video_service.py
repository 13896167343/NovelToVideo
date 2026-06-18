"""视频生成服务"""
import os
from PIL import Image, ImageDraw, ImageFont
from app.models.text_segment import TextSegment
from app.models.video_segment import VideoSegment
from app.models.task import Task
from app.models.project import Project
from app.models.temp_video_segment import TempVideoSegment
from app.utils.logger import get_logger
from app.utils.file_handler import FileHandler
from config import DefaultConfig

logger = get_logger(__name__)


class VideoService:
    """视频生成服务类"""
    
    @staticmethod
    def generate_and_save_queue(project_id):
        """
        自动生成视频合成队列并保存到数据库
        
        流程：
        1. 获取所有已完成的音频
        2. 根据 segment_duration 自动分组
        3. 为每组创建 TempVideoSegment 和 VideoSynthesisQueue 记录
        
        Args:
            project_id: 项目ID
            
        Returns:
            成功标志
        """
        try:
            # 获取项目信息
            project = Project.get_by_id(project_id)
            if not project:
                logger.error(f'项目不存在: 项目ID={project_id}')
                return False
            
            config = project.config
            
            # 获取所有已完成的音频
            completed_segments = TextSegment.get_completed_segments(project_id)
            
            if not completed_segments:
                logger.warning(f'没有已完成的音频: 项目ID={project_id}')
                return False
            
            # 收集音频时长信息
            segment_durations = {}
            total_duration = 0
            
            for segment in completed_segments:
                try:
                    # 优先从数据库读取时长
                    if segment.audio_duration is not None and segment.audio_duration > 0:
                        duration = segment.audio_duration
                    else:
                        # 如果数据库没有时长，使用 ffprobe 获取音频时长
                        import subprocess
                        audio_path = segment.get_absolute_audio_path()
                        if os.path.exists(audio_path):
                            try:
                                result = subprocess.run(
                                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
                                     '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                                    capture_output=True, text=True, encoding='utf-8'
                                )
                                duration = float(result.stdout.strip())
                            except Exception as e:
                                logger.warning(f'读取音频时长失败: {audio_path}, {e}')
                                duration = 0
                        else:
                            logger.warning(f'音频文件不存在: {audio_path}')
                            duration = 0
                    
                    segment_durations[segment.id] = duration
                    total_duration += duration
                except Exception as e:
                    logger.error(f'读取音频时长失败: segment_id={segment.id}, {str(e)}')
                    segment_durations[segment.id] = 0
            
            logger.info(f'收集到音频时长信息: 项目ID={project_id}, 总时长={total_duration}s, 音频数={len(completed_segments)}')
            
            # 获取配置参数
            segment_duration = config.get('segment_duration', DefaultConfig.DEFAULT_SEGMENT_DURATION)
            output_path = project.get_absolute_output_path()
            safe_name = FileHandler.safe_filename(project.name)
            
            # 分组逻辑：选择时长最接近 segment_duration 的音频组合
            video_index = 1
            selected_segment_ids = set()  # 已选择的音频ID
            queue_count = 0
            
            logger.info(f'开始分组生成队列: segment_duration={segment_duration}s')
            
            while len(selected_segment_ids) < len(completed_segments):
                # 从未选择的音频中选择
                remaining_segments = [s for s in completed_segments if s.id not in selected_segment_ids]
                
                if not remaining_segments:
                    break
                
                # 使用贪心算法选择时长最接近 segment_duration 的组合
                best_group = []
                best_group_duration = 0
                best_diff = float('inf')
                
                # 尝试所有可能的组合（简化版：按顺序贪心选择）
                current_group = []
                current_duration = 0
                
                for segment in remaining_segments:
                    seg_duration = segment_durations.get(segment.id, 0)
                    
                    # 如果加入这个音频后时长更接近目标，就加入
                    if current_duration + seg_duration <= segment_duration:
                        current_group.append(segment)
                        current_duration += seg_duration
                    elif abs((current_duration + seg_duration) - segment_duration) < abs(current_duration - segment_duration):
                        # 即使超过目标也加入，因为更接近目标
                        current_group.append(segment)
                        current_duration += seg_duration
                        break
                    else:
                        # 不加入，准备下一组
                        break
                
                # 如果当前组为空，至少选择一个音频
                if not current_group:
                    current_group = [remaining_segments[0]]
                    current_duration = segment_durations.get(current_group[0].id, 0)
                
                # 创建 TempVideoSegment 记录
                temp_segment_ids = []
                for segment in current_group:
                    try:
                        # 为每个音频创建临时视频片段记录
                        # 注意：存储相对路径，基于 TEMP_VIDEO_DIR
                        relative_temp_video_path = os.path.join(
                            str(project_id),
                            f'segment_{segment.id}.mp4'
                        )
                        temp_segment_id = TempVideoSegment.create(
                            project_id=project_id,
                            text_segment_id=segment.id,
                            temp_video_path=relative_temp_video_path
                        )
                        temp_segment_ids.append(temp_segment_id)
                        selected_segment_ids.add(segment.id)
                    except Exception as e:
                        logger.error(f'创建TempVideoSegment失败: segment_id={segment.id}, {str(e)}')
                
                # 创建 VideoSynthesisQueue 记录
                if temp_segment_ids:
                    try:
                        # 存储相对路径，基于项目的输出路径
                        relative_output_video_path = f'{safe_name}_{video_index:03d}.{config.get("format", "mp4")}'
                        queue_id = VideoSynthesisQueue.create(
                            project_id=project_id,
                            video_index=video_index,
                            output_video_path=relative_output_video_path,
                            temp_segment_ids=temp_segment_ids,
                            total_duration=current_duration
                        )
                        logger.info(f'创建队列记录: queue_id={queue_id}, video_index={video_index}, segments={len(temp_segment_ids)}, duration={current_duration:.1f}s')
                        queue_count += 1
                        video_index += 1
                    except Exception as e:
                        logger.error(f'创建VideoSynthesisQueue失败: video_index={video_index}, {str(e)})')
            
            logger.info(f'队列生成完成: 项目ID={project_id}, 总队列数={queue_count}')
            return True
            
        except Exception as e:
            logger.error(f'生成队列失败: 项目ID={project_id}, {str(e)}', exc_info=True)
            return False
    
    @staticmethod
    def generate_project_videos(project_id):
        """
        生成项目的所有视频
        
        流程：
        1. 检查音频合成进度是否达到100%
        2. 生成背景图片
        3. 调用新流程方法处理视频合成
        
        Args:
            project_id: 项目ID
            
        Returns:
            (成功标志, 错误信息)
        """
        task_id = None
        project = None
        try:
            # 创建任务
            task_id = Task.create(project_id, Task.TYPE_VIDEO_GENERATION)
            Task.update_status(task_id, Task.STATUS_RUNNING)
            
            # 获取项目信息
            project = Project.get_by_id(project_id)
            if not project:
                error_msg = '项目不存在'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                return False, error_msg
                
            config = project.config
            
            # 获取所有音频段落
            all_segments = TextSegment.get_by_project(project_id)
            completed_segments = TextSegment.get_completed_segments(project_id)
            
            # 检查音频合成进度是否达到100%
            if len(all_segments) == 0:
                error_msg = '没有音频段落'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                return False, error_msg
            
            # 计算音频完成进度
            audio_progress = len(completed_segments) / len(all_segments) * 100
            
            # 只有当音频合成进度达到100%时才允许开始视频合成
            if audio_progress < 100:
                error_msg = f'音频合成进度未达到100% ({audio_progress:.1f}%)，无法开始视频合成'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                return False, error_msg
            
            segments = completed_segments
            if not segments:
                error_msg = '没有已完成的音频段落'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                return False, error_msg
            
            logger.info(f'开始视频生成: 项目ID={project_id}, 段落数={len(segments)}')
            
            # 确保临时目录存在
            temp_image_dir = os.path.join(DefaultConfig.TEMP_IMAGE_DIR, str(project_id))
            temp_video_dir = os.path.join(DefaultConfig.TEMP_VIDEO_DIR, str(project_id))
            FileHandler.ensure_dir(temp_image_dir)
            FileHandler.ensure_dir(temp_video_dir)
            
            # 检查磁盘空间是否足够
            if not VideoService._check_disk_space(segments, temp_video_dir):
                error_msg = '磁盘空间不足，无法生成视频'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                return False, error_msg
            
            # 生成背景图片
            background_image = VideoService._generate_background_image(
                project.name,
                config,
                temp_image_dir
            )
            
            # 新流程：按 segment_duration 分组合成视频
            logger.info('开始按分组时长合成视频...')
            segment_duration = config.get('segment_duration', DefaultConfig.DEFAULT_SEGMENT_DURATION)
            output_path = project.get_absolute_output_path()
            safe_name = FileHandler.safe_filename(project.name)
            
            # 使用新的分组合成逻辑
            success = VideoService._synthesize_videos_by_duration(
                project_id,
                segments,
                config,
                background_image,
                segment_duration,
                temp_video_dir,
                output_path,
                safe_name,
                task_id
            )
            
            if not success:
                error_msg = '视频合成失败'
                Task.update_status(task_id, Task.STATUS_FAILED, error_msg)
                return False, error_msg
            
            Task.update_status(task_id, Task.STATUS_COMPLETED)
            Project.update_status(project_id, Project.STATUS_COMPLETED)
            
            logger.info(f'视频生成完成: 项目ID={project_id}')
            return True, None
            
        except Exception as e:
            logger.error(f'视频生成失败: {str(e)}', exc_info=True)
            if task_id:
                Task.update_status(task_id, Task.STATUS_FAILED, str(e))
            if project:
                Project.update_status(project_id, Project.STATUS_FAILED)
            return False, str(e)
    
    @staticmethod
    def _synthesize_videos_by_duration(project_id, segments, config, background_image,
                                      segment_duration, temp_video_dir, output_path,
                                      safe_name, task_id):
        """
        按 segment_duration 分组合成视频
        
        流程：
        1. 清理临时目录中的孤立文件
        2. 确保输出目录存在
        3. 调用新流程方法处理视频合成
        
        Args:
            project_id: 项目ID
            segments: 已完成的音频段落列表
            config: 配置字典
            background_image: 背景图片路径
            segment_duration: 目标分段时长(秒)
            temp_video_dir: 临时视频目录
            output_path: 输出目录
            safe_name: 安全的文件名
            task_id: 任务ID
            
        Returns:
            成功标志
        """
        try:
            VideoService._cleanup_orphaned_temp_files(project_id, temp_video_dir)
            FileHandler.ensure_dir(output_path)
            
            return VideoService._synthesize_from_queue(
                project_id,
                temp_video_dir,
                output_path,
                background_image,
                config,
                task_id
            )
            
        except Exception as e:
            logger.error(f'按时长分组合成视频失败: {str(e)}', exc_info=True)
            return False
    
    @staticmethod
    def _synthesize_from_queue(project_id, temp_video_dir, output_path, background_image, config, task_id):
        """
        使用 VideoSynthesisQueue 队列驱动的视频合成
        
        新流程：
        1. 根据用户设定的视频时长合成固定时长的临时视频
        2. 将所有已完成的TTS音频按顺序合成为完整长音频，然后按用户设定的时长分割为固定时长的音频
        3. 将音频和视频合成对应的完整视频文件
        4. 最后删除临时文件，只保留最终视频和TTS音频
        
        Args:
            project_id: 项目ID
            temp_video_dir: 临时视频目录
            output_path: 输出目录
            background_image: 背景图片路径
            config: 配置字典
            task_id: 任务ID
            
        Returns:
            成功标志
        """
        import subprocess
        import shutil
        
        try:
            logger.info(f'开始使用新流程合成视频: project_id={project_id}')
            
            segment_duration = config.get('segment_duration', DefaultConfig.DEFAULT_SEGMENT_DURATION)
            project = Project.get_by_id(project_id)
            safe_name = FileHandler.safe_filename(project.name) if project else 'output'
            
            all_segments = TextSegment.get_completed_segments(project_id)
            if not all_segments:
                logger.error(f'没有已完成的TTS音频: project_id={project_id}')
                return False
            
            logger.info(f'已完成的TTS音频数量: {len(all_segments)}')
            
            FileHandler.ensure_dir(output_path)
            FileHandler.ensure_dir(temp_video_dir)
            
            logger.info(f'步骤1: 将所有TTS音频按顺序合并为一个长音频')
            audio_files = []
            for segment in all_segments:
                audio_abs_path = segment.get_absolute_audio_path()
                if os.path.exists(audio_abs_path) and os.path.getsize(audio_abs_path) > 0:
                    audio_files.append(audio_abs_path)
                else:
                    logger.warning(f'TTS音频文件缺失或为空，跳过: segment_id={segment.id}, path={audio_abs_path}')
            
            if not audio_files:
                logger.error(f'没有可用的TTS音频文件')
                return False
            
            long_audio_file = os.path.join(temp_video_dir, 'long_audio_all.mp3')
            VideoService._merge_audio_files(audio_files, long_audio_file)
            logger.info(f'步骤1 完成: 合并了 {len(audio_files)} 个音频文件')
            
            logger.info(f'步骤2: 按固定时长({segment_duration}s)分割长音频')
            audio_segments = VideoService._split_audio_by_duration(long_audio_file, temp_video_dir, segment_duration)
            
            if not audio_segments:
                logger.error(f'音频分割失败')
                return False
            
            logger.info(f'步骤2 完成: 分割为 {len(audio_segments)} 个音频片段')
            
            logger.info(f'步骤3: 生成固定时长的临时视频')
            total_segments = len(audio_segments)
            temp_video_file = os.path.join(temp_video_dir, 'temp_video_fixed.mp4')
            
            step3a_cmd = [
                'ffmpeg',
                '-loop', '1',
                '-r', '1',
                '-t', str(segment_duration),
                '-f', 'image2',
                '-i', background_image,
                '-c:v', 'libx264',
                '-crf', '24',
                '-pix_fmt', 'yuv420p',
                '-y',
                temp_video_file
            ]
            
            logger.info(f'生成固定时长临时视频: duration={segment_duration}s')
            result = subprocess.run(step3a_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
            if result.returncode != 0:
                logger.error(f'生成固定时长临时视频失败: {result.stderr}')
                return False
            logger.info(f'步骤3 完成: 固定时长临时视频已生成')
            
            logger.info(f'步骤4: 将临时视频与每个音频片段合成最终视频')
            
            for idx, audio_segment in enumerate(audio_segments):
                try:
                    final_video_file = os.path.join(output_path, f'{safe_name}_{idx + 1:03d}.mp4')
                    
                    step4_cmd = [
                        'ffmpeg',
                        '-i', temp_video_file,
                        '-i', audio_segment,
                        '-c:v', 'copy',
                        '-c:a', 'copy',
                        '-y',
                        final_video_file
                    ]
                    
                    logger.info(f'合成最终视频 {idx + 1}/{total_segments}: {os.path.basename(final_video_file)}')
                    result = subprocess.run(step4_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
                    if result.returncode != 0:
                        logger.error(f'合成最终视频失败: {result.stderr}')
                        continue
                    
                    VideoSegment.create(project_id, idx, segment_duration, os.path.basename(final_video_file))
                    VideoSegment.update_status(VideoSegment.get_by_project(project_id)[-1].id, VideoSegment.STATUS_COMPLETED)
                    
                    logger.info(f'✓ 视频段落 {idx + 1}/{total_segments} 处理完成')
                    
                except Exception as e:
                    logger.error(f'处理音频片段失败: idx={idx}, error={str(e)}')
                    continue
                
                finally:
                    progress = (idx + 1) / total_segments * 100
                    Task.update_progress(task_id, progress)
            
            logger.info(f'步骤5: 清理临时文件')
            deleted_count = 0
            
            try:
                if os.path.exists(temp_video_file):
                    os.remove(temp_video_file)
                    deleted_count += 1
                    logger.info(f'已删除临时视频: temp_video_fixed.mp4')
            except Exception as e:
                logger.warning(f'删除临时视频失败: {e}')
            
            try:
                if os.path.exists(long_audio_file):
                    os.remove(long_audio_file)
                    deleted_count += 1
                    logger.info(f'已删除长音频: long_audio_all.mp3')
            except Exception as e:
                logger.warning(f'删除长音频失败: {e}')
            
            for f in audio_segments:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                        deleted_count += 1
                except:
                    pass
            
            logger.info(f'步骤5 完成: 已删除 {deleted_count} 个临时文件')
            logger.info(f'✓ 视频合成全部完成，临时文件已清理')
            return True
            
        except Exception as e:
            logger.error(f'视频合成失败: {str(e)}', exc_info=True)
            return False
    
    @staticmethod
    def _cleanup_orphaned_temp_files(project_id, temp_video_dir):
        """
        清理临时目录中的孤立文件
        
        异常中断时，临时目录可能残留文件。本方法通过查询数据库状态来判断：
        - 如果 temp_video_segments 中 status='pending' 或为空，则删除对应临时文件
        - 如果 status='synthesized'，则保留（正在处理或已完成）
        - 如果 status='deleted'，则删除（已标记删除）
        
        这样可以支持断点续传，项目异常中断后重新开始时不会丢失已合成的临时视频
        
        Args:
            project_id: 项目ID
            temp_video_dir: 临时视频目录
        """
        if not os.path.exists(temp_video_dir):
            return
        
        try:
            # 获取该项目的所有临时视频段状态
            temp_segments = TempVideoSegment.get_by_project(project_id)
            temp_segment_map = {seg.id: seg for seg in temp_segments}
            
            cleaned_count = 0
            preserved_count = 0
            
            for filename in os.listdir(temp_video_dir):
                file_path = os.path.join(temp_video_dir, filename)
                
                # 只处理临时文件（segment_*.mp4 和 *.m4a）
                if filename.startswith('segment_') or filename.endswith('.m4a'):
                    try:
                        if not os.path.isfile(file_path):
                            continue
                        
                        # 对于 segment_*.m4a 类型的音频临时文件，直接删除
                        if filename.endswith('.m4a'):
                            os.remove(file_path)
                            cleaned_count += 1
                            continue
                        
                        # 对于 segment_*.mp4，尝试从数据库查询对应的临时视频段
                        # 匹配格式：segment_{project_id}/segment_{text_segment_id}.mp4
                        should_delete = True
                        
                        # 检查是否存在对应的数据库记录
                        for temp_seg in temp_segments:
                            temp_path = temp_seg.temp_video_path
                            # temp_path 可能是相对路径或包含项目ID的路径
                            if temp_path and filename in temp_path:
                                # 如果找到对应的数据库记录，检查状态
                                if temp_seg.status == TempVideoSegment.STATUS_SYNTHESIZED:
                                    # 已合成，保留文件
                                    should_delete = False
                                    preserved_count += 1
                                    break
                                elif temp_seg.status == TempVideoSegment.STATUS_DELETED:
                                    # 已标记删除，删除文件
                                    should_delete = True
                                    break
                        
                        # 删除文件
                        if should_delete and os.path.isfile(file_path):
                            os.remove(file_path)
                            cleaned_count += 1
                    
                    except Exception as e:
                        logger.warning(f'清理临时文件失败: {filename}, 错误: {str(e)}')
            
            if cleaned_count > 0 or preserved_count > 0:
                logger.info(f'清理临时文件: 删除 {cleaned_count} 个孤立文件，保留 {preserved_count} 个已合成文件')
        except Exception as e:
            logger.warning(f'清理临时目录失败: {str(e)}')
    
    @staticmethod
    def _validate_output_file_integrity(output_file):
        """
        验证输出视频文件的完整性
        
        检查项：
        1. 文件是否存在
        2. 文件大小是否大于0
        3. 文件是否是有效的视频（验证時長）
        
        Args:
            output_file: 视频文件路径
            
        Returns:
            True 如果文件有效，False 否则
        """
        if not os.path.exists(output_file):
            return False
        
        if os.path.getsize(output_file) == 0:
            logger.warning(f'输出文件大小为0: {output_file}')
            return False
        
        # 验证视频时长是否有效（通过 ffprobe）
        try:
            import subprocess
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
                 '-of', 'default=noprint_wrappers=1:nokey=1', output_file],
                capture_output=True, text=True, encoding='utf-8'
            )
            duration = float(result.stdout.strip())
            if duration <= 0:
                logger.warning(f'输出视频时长不有效: {output_file}, duration={duration}')
                return False
            return True
        except Exception as e:
            logger.warning(f'输出视频文件验证失败（可能不完整）: {output_file}, 错误: {str(e)}')
            return False
    
    @staticmethod
    def _select_segments_by_target_duration(segments, segment_durations, synthesized_ids, target_duration):
        """
        从未合成音频中选择相加时间最接近 target_duration 的音频
        
        使用贪心算法：按顺序遍历未合成的音频，选择总时长最接近目标时长的组合
        
        Args:
            segments: 所有音频段落列表
            segment_durations: 音频时长字典 {segment_id: duration}
            synthesized_ids: 已合成的音频ID集合
            target_duration: 目标总时长
            
        Returns:
            选中的 TextSegment 对象列表
        """
        # 获取未合成的音频段落和时长
        pending_segments = [s for s in segments if s.id not in synthesized_ids]
        pending_durations = {s.id: segment_durations[s.id] for s in pending_segments if s.id in segment_durations}
        
        if not pending_segments:
            return []
        
        # 使用贪心算法选择最接近目标时长的音频组合
        selected = []
        current_duration = 0
        
        for segment in pending_segments:
            if segment.id not in pending_durations:
                continue
            
            duration = pending_durations[segment.id]
            # 如果加上这个音频后时长不超过目标时长，则加入
            if current_duration + duration <= target_duration:
                selected.append(segment)
                current_duration += duration
            # 如果已经超过目标时长的一半，停止添加
            elif current_duration > target_duration * 0.5:
                break
        
        # 如果没有选中任何音频，选择第一个未合成的音频
        if not selected and pending_segments:
            selected = [pending_segments[0]]
        
        return selected
    
    @staticmethod
    def _merge_and_save_videos(temp_video_files, output_file, config, temp_video_dir):
        """
        合并多个视频文件并保存到输出位置
        
        使用 FFmpeg -c copy 直接拼接视频（无需重新编码）
        
        Args:
            temp_video_files: 临时视频文件列表
            output_file: 输出文件路径
            config: 配置字典
            temp_video_dir: 临时视频目录
        """
        import subprocess
        
        if not temp_video_files:
            raise Exception('没有临时视频文件')
        
        try:
            # 如果只有一个文件，直接复制
            if len(temp_video_files) == 1:
                logger.info(f'只有一个视频文件，直接复制')
                output_dir = os.path.dirname(output_file)
                FileHandler.ensure_dir(output_dir)
                import shutil
                shutil.copy(temp_video_files[0], output_file)
                logger.info(f'文件复制完成: {output_file}')
                return
            
            # 使用 FFmpeg -c copy 直接拼接
            logger.info(f'开始使用 FFmpeg -c copy 拼接 {len(temp_video_files)} 个视频')
            
            # 创建文件列表（FFmpeg concat demuxer 需要）
            concat_file = os.path.join(temp_video_dir, 'concat_list.txt')
            with open(concat_file, 'w', encoding='utf-8') as f:
                for video_file in temp_video_files:
                    # FFmpeg concat demuxer 需要使用单引号引用文件路径
                    f.write(f"file '{os.path.abspath(video_file)}'\n")
            
            logger.info(f'创建了 concat 文件: {concat_file}')
            
            # 执行 FFmpeg 命令
            output_dir = os.path.dirname(output_file)
            FileHandler.ensure_dir(output_dir)
            
            # 构造 FFmpeg 命令：使用 -c copy 不重新编码
            ffmpeg_cmd = [
                'ffmpeg',
                '-f', 'concat',              # 使用 concat demuxer
                '-safe', '0',                # 允许绝对路径
                '-i', concat_file,           # 输入文件列表
                '-c', 'copy',                # 直接复制，不重新编码
                '-y',                        # 覆盖输出文件
                output_file
            ]
            
            logger.info(f'FFmpeg 命令: {" ".join(ffmpeg_cmd)}')
            
            # 执行命令（使用 UTF-8 编码以支持中文路径）
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=3600  # 最长 1 小时
            )
            
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else '未知错误'
                logger.error(f'FFmpeg 拼接失败: {error_msg}')
                raise Exception(f'FFmpeg 拼接失败: {error_msg}')
            
            logger.info(f'视频拼接成功: {output_file}')
            
            # 清理 concat 文件
            try:
                os.remove(concat_file)
                logger.debug(f'清理 concat 文件')
            except Exception as e:
                logger.warning(f'清理 concat 文件失败: {str(e)}')
        
        except subprocess.TimeoutExpired:
            logger.error(f'FFmpeg 拼接超时')
            raise Exception('FFmpeg 拼接超时')
        except Exception as e:
            logger.error(f'视频拼接失败: {str(e)}')
            raise
    
    @staticmethod
    def _merge_audio_files(audio_files, output_file):
        """
        使用 FFmpeg 合并多个音频文件为一个长音频

        跳过空文件或缺失文件，避免 ffmpeg concat demuxer 中断整个合并流程。

        Args:
            audio_files: 音频文件路径列表
            output_file: 输出音频文件路径

        Returns:
            成功标志
        """
        import subprocess

        if not audio_files:
            raise Exception('没有音频文件')

        # 过滤空文件/缺失文件，避免 ffmpeg 在 concat 时遇错中断整次合并
        valid_audio_files = []
        skipped = []
        for audio_file in audio_files:
            if not os.path.exists(audio_file):
                skipped.append((audio_file, '缺失'))
                logger.warning(f'跳过缺失的音频文件: {audio_file}')
                continue
            try:
                if os.path.getsize(audio_file) == 0:
                    skipped.append((audio_file, '空文件'))
                    logger.warning(f'跳过空音频文件: {audio_file}')
                    continue
            except OSError as e:
                skipped.append((audio_file, f'读取失败: {e}'))
                logger.warning(f'无法读取音频文件: {audio_file}, {e}')
                continue
            valid_audio_files.append(audio_file)

        if not valid_audio_files:
            raise Exception('没有可用的音频文件（全部缺失或为空）')

        if skipped:
            logger.warning(f'合并前已过滤 {len(skipped)} 个无效音频文件')

        try:
            if len(valid_audio_files) == 1:
                logger.info(f'只有一个有效音频文件，直接复制')
                output_dir = os.path.dirname(output_file)
                FileHandler.ensure_dir(output_dir)
                import shutil
                shutil.copy(valid_audio_files[0], output_file)
                logger.info(f'音频文件复制完成: {output_file}')
                return True

            logger.info(f'开始合并 {len(valid_audio_files)} 个音频文件')

            temp_dir = os.path.dirname(output_file)
            FileHandler.ensure_dir(temp_dir)

            concat_file = os.path.join(temp_dir, 'audio_concat_list.txt')
            with open(concat_file, 'w', encoding='utf-8') as f:
                for audio_file in valid_audio_files:
                    f.write(f"file '{os.path.abspath(audio_file)}'\n")

            merge_cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-c', 'copy',
                '-y',
                output_file
            ]

            logger.info(f'FFmpeg 音频合并命令: {" ".join(merge_cmd)}')
            result = subprocess.run(merge_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)

            try:
                if os.path.exists(concat_file):
                    os.remove(concat_file)
            except:
                pass

            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else '未知错误'
                logger.error(f'FFmpeg 音频合并失败: {error_msg}')
                raise Exception(f'FFmpeg 音频合并失败: {error_msg}')

            logger.info(f'音频合并成功: {output_file}')
            return True

        except subprocess.TimeoutExpired:
            logger.error(f'FFmpeg 音频合并超时')
            raise Exception('FFmpeg 音频合并超时')
        except Exception as e:
            logger.error(f'音频合并失败: {str(e)}')
            raise
    
    @staticmethod
    def _split_audio_by_duration(input_audio, output_dir, duration):
        """
        按固定时长分割音频文件
        
        Args:
            input_audio: 输入音频文件路径
            output_dir: 输出目录
            duration: 每个片段的时长（秒）
            
        Returns:
            分割后的音频文件路径列表
        """
        import subprocess
        
        if not os.path.exists(input_audio):
            raise Exception(f'音频文件不存在: {input_audio}')
        
        try:
            FileHandler.ensure_dir(output_dir)
            
            audio_files = []
            segment_index = 0
            current_start = 0.0
            
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
                 '-of', 'default=noprint_wrappers=1:nokey=1', input_audio],
                capture_output=True, text=True, encoding='utf-8', timeout=30
            )
            total_duration = float(result.stdout.strip()) if result.returncode == 0 else 0
            logger.info(f'音频总时长: {total_duration}s')
            
            logger.info(f'开始按 {duration}s 分割音频: {input_audio}')
            
            while True:
                if current_start >= total_duration:
                    logger.info(f'已到达音频末尾，停止分割')
                    break
                
                segment_output = os.path.join(output_dir, f'audio_segment_{segment_index:03d}.mp3')
                
                ffmpeg_cmd = [
                    'ffmpeg',
                    '-i', input_audio,
                    '-ss', str(current_start),
                    '-t', str(duration),
                    '-c', 'copy',
                    '-y',
                    segment_output
                ]
                
                result = subprocess.run(
                    ffmpeg_cmd,
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600
                )
                
                if result.returncode != 0:
                    logger.warning(f'分割音频片段失败: segment_index={segment_index}, {result.stderr}')
                    break
                
                file_size = os.path.getsize(segment_output) if os.path.exists(segment_output) else 0
                if file_size > 10240:
                    audio_files.append(segment_output)
                    segment_index += 1
                    current_start += duration
                else:
                    logger.info(f'音频片段过小({file_size}字节)，视为已结束')
                    if os.path.exists(segment_output):
                        os.remove(segment_output)
                    break
            
            logger.info(f'音频分割完成: 共 {len(audio_files)} 个片段')
            return audio_files
            
        except Exception as e:
            logger.error(f'音频分割失败: {str(e)}')
            raise
    
    @staticmethod
    def _check_disk_space(segments, temp_dir):
        """
        检查磁盘空间是否足够
        
        Args:
            segments: 音频段落列表
            temp_dir: 临时目录路径
            
        Returns:
            bool: 是否有足够的磁盘空间
        """
        try:
            # 估算所需空间：假设每个音频段落生成的视频大约是音频的3倍大小
            estimated_size = 0
            for segment in segments:
                audio_path = segment.get_absolute_audio_path()
                if os.path.exists(audio_path):
                    audio_size = os.path.getsize(audio_path)
                    estimated_size += audio_size * 3  # 视频大约是音频的3倍
            
            # 检查临时目录可用空间
            stat = os.statvfs(temp_dir) if hasattr(os, 'statvfs') else None
            if stat:
                free_space = stat.f_frsize * stat.f_bavail
                return free_space > estimated_size * 2  # 需要至少2倍的估计空间
            
            # 如果无法检查磁盘空间，则假设有足够空间
            return True
        except Exception as e:
            logger.warning(f'检查磁盘空间时出错: {str(e)}')
            return True  # 出错时继续执行，但记录警告
    
    @staticmethod
    def _generate_background_image(project_name, config, temp_image_dir):
        """
        生成背景图片
        
        Args:
            project_name: 项目名称
            config: 配置字典
            temp_image_dir: 临时图片目录
            
        Returns:
            背景图片路径
        """
        # 检查是否使用自定义背景图片
        background_option = config.get('background_option', 'default')
        custom_background_path = config.get('custom_background_path')
        
        if background_option == 'custom' and custom_background_path and os.path.exists(custom_background_path):
            # 使用自定义背景图片
            logger.info(f'使用自定义背景图片: {custom_background_path}')
            
            # 获取目标分辨率
            resolution = config.get('resolution', DefaultConfig.DEFAULT_RESOLUTION)
            width, height = resolution
            
            # 调整自定义图片大小以匹配目标分辨率
            try:
                custom_image = Image.open(custom_background_path)
                # 调整图片大小以适应目标分辨率（保持宽高比）
                custom_image = custom_image.resize((width, height), Image.Resampling.LANCZOS)
                
                # 保存调整后的图片到临时目录
                adjusted_image_path = os.path.join(temp_image_dir, 'custom_background.png')
                custom_image.save(adjusted_image_path)
                custom_image.close()
                
                logger.info(f'自定义背景图片调整完成: {adjusted_image_path}')
                return adjusted_image_path
            except Exception as e:
                logger.error(f'处理自定义背景图片失败: {e}')
                # 如果处理失败，继续使用默认背景生成
        
        # 使用默认背景生成（原有的逻辑）
        resolution = config.get('resolution', DefaultConfig.DEFAULT_RESOLUTION)
        width, height = resolution
        
        # 创建黑色背景
        image = Image.new('RGB', (width, height), color=0)
        draw = ImageDraw.Draw(image)
        
        # 使用默认字体
        font_size = int(height * 0.08)  # 字体大小为高度的8%
        try:
            # 尝试使用系统中文字体
            font = ImageFont.truetype("msyh.ttc", font_size)  # 微软雅黑
        except:
            try:
                font = ImageFont.truetype("simhei.ttf", font_size)  # 黑体
            except:
                font = ImageFont.load_default()
        
        # 计算文字位置(居中)
        text = project_name
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) / 2
        y = (height - text_height) / 2
        
        # 绘制白色文字
        draw.text((x, y), text, fill=(255, 255, 255), font=font)
        
        # 保存图片
        image_path = os.path.join(temp_image_dir, 'background.png')
        image.save(image_path)
        
        logger.info(f'背景图片生成成功: {image_path}')
        return image_path
    
