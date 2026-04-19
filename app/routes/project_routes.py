"""项目相关路由"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.services.project_service import ProjectService
from app.services.task_scheduler import TaskScheduler
from app.models.project import Project
from app.models.text_segment import TextSegment
from app.utils.logger import get_logger
from app.utils.file_handler import FileHandler
from config import DefaultConfig
import os
import uuid
import time

logger = get_logger(__name__)

project_bp = Blueprint('project', __name__, url_prefix='/project')


@project_bp.route('/')
def index():
    """项目列表页面"""
    try:
        projects = ProjectService.get_all_projects()
        return render_template('project_list.html', projects=projects)
    except Exception as e:
        logger.error(f'获取项目列表失败: {str(e)}', exc_info=True)
        flash(f'获取项目列表失败: {str(e)}', 'error')
        return render_template('project_list.html', projects=[])


@project_bp.route('/upload-file', methods=['POST'])
def upload_file():
    """异步文件上传接口 - 支持大文件上传，避免浏览器卡死"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有文件'}), 400
        
        file = request.files['file']
        
        if not file.filename.endswith('.txt'):
            return jsonify({'success': False, 'error': '仅支持.txt文件'}), 400
        
        # 检查文件大小（50MB限制）
        max_size = 50 * 1024 * 1024
        file.seek(0, 2)  # 跳到文件末尾
        file_size = file.tell()
        file.seek(0)  # 回到文件开头
        
        if file_size > max_size:
            return jsonify({'success': False, 'error': '文件大小不能超过50MB'}), 400
        
        # 生成临时文件名
        temp_id = str(uuid.uuid4())
        temp_filename = f"{temp_id}.txt"
        temp_dir = DefaultConfig.TEMP_DIR
        FileHandler.ensure_dir(temp_dir)
        temp_path = os.path.join(temp_dir, temp_filename)
        
        # 保存文件
        file.save(temp_path)
        logger.info(f'文件上传成功: {temp_path}, 大小: {file_size} bytes')
        
        # 只读取前100KB进行编码检测和预览，避免内存占用
        preview_size = min(file_size, 100 * 1024)  # 最多读取100KB
        preview_data, detected_encoding = FileHandler.read_text_file(temp_path)
        
        if preview_data is None:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return jsonify({'success': False, 'error': '无法读取文件内容，请检查文件编码'}), 400
        
        # 获取预览内容（前1000字符）
        content_preview = preview_data[:1000]
        preview_char_count = len(content_preview)
        
        # 估算总字符数（基于文件大小和预览的平均字符长度）
        # 假设文件内容均匀分布
        estimated_total_chars = int((file_size / preview_size) * preview_char_count) if preview_size > 0 else 0
        
        # 返回结果（包含临时文件ID供后续使用，不返回完整内容）
        return jsonify({
            'success': True,
            'message': f'上传成功，文件大小: {(file_size/1024/1024):.2f}MB',
            'file_size': file_size,
            'encoding': detected_encoding,
            'temp_id': temp_id,
            'content_preview': content_preview,
            'estimated_chars': estimated_total_chars
        })
        
    except Exception as e:
        logger.error(f'文件上传失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'上传失败: {str(e)}'}), 500


@project_bp.route('/get-file-content/<temp_id>', methods=['GET'])
def get_file_content(temp_id):
    """获取临时文件内容 - 支持分块加载，避免大文件导致浏览器卡死"""
    try:
        temp_filename = f"{temp_id}.txt"
        temp_path = os.path.join(DefaultConfig.TEMP_DIR, temp_filename)
        
        if not os.path.exists(temp_path):
            return jsonify({'success': False, 'error': '文件不存在或已过期'}), 404
        
        # 读取文件内容
        content, detected_encoding = FileHandler.read_text_file(temp_path)
        
        if content is None:
            return jsonify({'success': False, 'error': '无法读取文件内容'}), 500
        
        char_count = len(content)
        
        return jsonify({
            'success': True,
            'content': content,
            'char_count': char_count,
            'encoding': detected_encoding
        })
        
    except Exception as e:
        logger.error(f'获取文件内容失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'获取失败: {str(e)}'}), 500


@project_bp.route('/create', methods=['GET', 'POST'])
def create():
    """创建项目"""
    if request.method == 'GET':
        return render_template('project_create.html')
    
    temp_file_path = None
    
    try:
        # 支持 JSON 和表单两种提交方式
        # 优先解析 JSON，若非 JSON 则回退到表单
        if request.is_json:
            data = request.get_json(silent=True) or {}
            name = str(data.get('name', '')).strip()
            description = str(data.get('description', '')).strip()
            temp_id = data.get('temp_id')
            text_content = str(data.get('text_content', '')).strip()
            # 解析配置（兼容字符串/列表/数值）
            resolution_val = data.get('resolution', '1920,1080')
            if isinstance(resolution_val, (list, tuple)) and len(resolution_val) == 2:
                resolution = (int(resolution_val[0]), int(resolution_val[1]))
            else:
                resolution = tuple(map(int, str(resolution_val).split(',')))
            fps = int(data.get('fps', 30))
            bitrate = str(data.get('bitrate', '2000k'))
            fmt = str(data.get('format', 'mp4'))
            segment_duration = int(data.get('segment_duration', 600))
            segment_mode = str(data.get('segment_mode', 'word_count'))
            max_words = int(data.get('max_words', 10000))
            voice = str(data.get('voice', 'zh-CN-XiaoxiaoNeural'))
            rate = str(data.get('rate', '+0%'))
            pitch = str(data.get('pitch', '+0Hz'))
            volume = str(data.get('volume', '+0%'))
        else:
            # 获取表单数据
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            temp_id = request.form.get('temp_id')
            text_content = request.form.get('text_content', '').strip()
            resolution = tuple(map(int, request.form.get('resolution', '1920,1080').split(',')))
            fps = int(request.form.get('fps', 30))
            bitrate = request.form.get('bitrate', '2000k')
            fmt = request.form.get('format', 'mp4')
            segment_duration = int(request.form.get('segment_duration', 600))
            segment_mode = request.form.get('segment_mode', 'word_count')
            max_words = int(request.form.get('max_words', 10000))
            voice = request.form.get('voice', 'zh-CN-XiaoxiaoNeural')
            rate = request.form.get('rate', '+0%')
            pitch = request.form.get('pitch', '+0Hz')
            volume = request.form.get('volume', '+0%')
        
        # 处理背景图片上传
        background_option = request.form.get('background_option', 'default')
        custom_background_path = None
        
        if background_option == 'custom' and 'background_image' in request.files:
            background_file = request.files['background_image']
            if background_file and background_file.filename:
                # 保存自定义背景图片
                safe_filename = FileHandler.safe_filename(background_file.filename)
                custom_background_path = os.path.join(DefaultConfig.TEMP_IMAGE_DIR, 'custom_backgrounds', safe_filename)
                FileHandler.ensure_dir(os.path.dirname(custom_background_path))
                background_file.save(custom_background_path)
        
        # 获取配置参数
        config = {
            'voice': voice,
            'rate': rate,
            'pitch': pitch,
            'volume': volume,
            'resolution': resolution,
            'fps': fps,
            'bitrate': bitrate,
            'format': fmt,
            'segment_duration': segment_duration,
            'segment_mode': segment_mode,
            'max_words': max_words,
            'background_option': background_option,
            'custom_background_path': custom_background_path
        }
        
        # 验证必填字段
        if not name:
            return jsonify({'success': False, 'error': '项目名称不能为空'}), 400
        
        # 处理临时文件或文本内容
        if temp_id:
            # 从文件上传方式：读取临时文件内容
            temp_filename = f"{temp_id}.txt"
            temp_file_path = os.path.join(DefaultConfig.TEMP_DIR, temp_filename)
            
            if not os.path.exists(temp_file_path):
                return jsonify({'success': False, 'error': '文件不存在或已过期，请重新上传'}), 400
            
            logger.info(f'正在读取临时文件: {temp_file_path}')
            
            # 读取完整文件内容
            text_content, detected_encoding = FileHandler.read_text_file(temp_file_path)
            
            if text_content is None:
                return jsonify({'success': False, 'error': '无法读取文件内容，请检查文件编码'}), 400
            
            # 检查字数限制（5000万字）
            char_count = len(text_content)
            if char_count > 50000000:
                return jsonify({'success': False, 'error': f'文件太大({char_count}字)，最多支持5000万字'}), 400
            
            logger.info(f'从临时文件读取成功，编码: {detected_encoding}, 字符数: {char_count}')
            
        elif not text_content:
            return jsonify({'success': False, 'error': '文本内容不能为空'}), 400
        else:
            # 从文本框粘贴方式：直接使用 text_content
            char_count = len(text_content)
            logger.info(f'从文本框读取成功，字符数: {char_count}')
        
        # 创建项目
        project_id, error = ProjectService.create_project(name, description, text_content, config)
        
        if error:
            return jsonify({'success': False, 'error': error}), 400
        
        # 创建成功后删除临时文件（如果存在）
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f'临时文件已删除: {temp_file_path}')
            except Exception as e:
                logger.warning(f'删除临时文件失败: {temp_file_path}, 错误: {str(e)}')
        
        # 不再自动提交任务，而是返回成功信息
        return jsonify({
            'success': True,
            'project_id': project_id,
            'message': f'项目创建成功，字符数: {char_count}',
            'char_count': char_count
        })
        
    except Exception as e:
        logger.error(f'创建项目失败: {str(e)}', exc_info=True)
        
        # 确保临时文件被清理
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f'异常时临时文件已清理: {temp_file_path}')
            except Exception:
                pass
        
        return jsonify({'success': False, 'error': f'创建项目失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>')
def detail(project_id):
    """项目详情页面"""
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            flash('项目不存在', 'error')
            return redirect(url_for('project.index'))
        
        stats = ProjectService.get_project_statistics(project_id)
        
        return render_template('project_detail.html', project=project, stats=stats)
        
    except Exception as e:
        logger.error(f'获取项目详情失败: {str(e)}', exc_info=True)
        flash(f'获取项目详情失败: {str(e)}', 'error')
        return redirect(url_for('project.index'))


@project_bp.route('/<int:project_id>/delete', methods=['POST'])
def delete(project_id):
    """删除项目"""
    try:
        success, error = ProjectService.delete_project(project_id)
        
        if error:
            return jsonify({'success': False, 'error': error}), 400
        
        return jsonify({'success': True, 'message': '项目删除成功'})
        
    except Exception as e:
        logger.error(f'删除项目失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'删除项目失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>/stats')
def stats(project_id):
    """获取项目统计信息"""
    try:
        stats = ProjectService.get_project_statistics(project_id)
        
        if stats is None:
            return jsonify({'success': False, 'error': '项目不存在'}), 404
        
        return jsonify({'success': True, 'data': stats})
        
    except Exception as e:
        logger.error(f'获取项目统计失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'获取统计信息失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>/retry/tts', methods=['POST'])
def retry_tts(project_id):
    """重试语音合成任务
    
    - 将项目内失败的段落重置为待处理
    - 提交新的语音合成任务到调度器
    """
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            return jsonify({'success': False, 'error': '项目不存在'}), 404

        # 重置失败段落为待处理
        TextSegment.reset_audio_status_by_project(
            project_id,
            TextSegment.AUDIO_STATUS_FAILED,
            TextSegment.AUDIO_STATUS_PENDING
        )

        # 提交语音合成任务
        TaskScheduler.submit_tts_task(project_id)

        return jsonify({'success': True, 'message': '已提交重试语音合成任务'})
    except Exception as e:
        logger.error(f'重试语音合成失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'重试失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>/generate/video', methods=['POST'])
def generate_video(project_id):
    """提交视频生成任务"""
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            return jsonify({'success': False, 'error': '项目不存在'}), 404

        # 更新项目状态为处理中
        Project.update_status(project_id, Project.STATUS_PROCESSING)
        
        TaskScheduler.submit_video_task(project_id)
        return jsonify({'success': True, 'message': '已提交视频生成任务'})
    except Exception as e:
        logger.error(f'提交视频生成任务失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'提交失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>/start', methods=['POST'])
def start_processing(project_id):
    """开始处理项目任务
    
    - 提交语音合成任务到调度器
    """
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            return jsonify({'success': False, 'error': '项目不存在'}), 404

        # 检查项目状态
        if project.status != 'pending':
            return jsonify({'success': False, 'error': '项目状态不是待处理，无法开始处理'}), 400

        # 提交语音合成任务
        TaskScheduler.submit_tts_task(project_id)
        
        # 更新项目状态为处理中
        Project.update_status(project_id, Project.STATUS_PROCESSING)

        return jsonify({'success': True, 'message': '已提交语音合成任务'})
    except Exception as e:
        logger.error(f'开始处理任务失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'开始处理任务失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>/resegment', methods=['POST'])
def resegment(project_id):
    """重新分段文本并重置项目为待处理
    
    - 读取现有段落重构原始文本
    - 根据传入配置重新分段并写入数据库
    - 项目重置为待处理，可继续进行语音合成
    """
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            return jsonify({'success': False, 'error': '项目不存在'}), 404

        # 解析参数（JSON或表单），覆盖项目默认配置
        def _to_int(val, default):
            """安全转换为 int，None 或非法字符串时返回默认值"""
            try:
                return int(val)
            except (TypeError, ValueError):
                try:
                    return int(default)
                except Exception:
                    return 0
        if request.is_json:
            data = request.get_json(silent=True) or {}
            segment_mode = str(data.get('segment_mode', project.config.get('segment_mode', 'word_count') if isinstance(project.config, dict) else 'word_count'))
            default_max = project.config.get('max_words', 10000) if isinstance(project.config, dict) else 10000
            max_words = _to_int(data.get('max_words', default_max), default_max)
        else:
            default_mode = project.config.get('segment_mode', 'word_count') if isinstance(project.config, dict) else 'word_count'
            segment_mode = request.form.get('segment_mode', default_mode) or default_mode
            default_max = project.config.get('max_words', 10000) if isinstance(project.config, dict) else 10000
            max_words = _to_int(request.form.get('max_words', default_max), default_max)

        # 执行重新分段
        success, error = ProjectService.resegment_project(project_id, segment_mode, max_words)
        if not success:
            return jsonify({'success': False, 'error': error or '重新分段失败'}), 400

        return jsonify({'success': True, 'message': '重新分段完成，已重置为待处理'})
    except Exception as e:
        logger.error(f'重新分段失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'重新分段失败: {str(e)}'}), 500


@project_bp.route('/<int:project_id>/video/queue')
def view_video_queue(project_id):
    """查看视频合成队列"""
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            flash('项目不存在', 'error')
            return redirect(url_for('project.index'))
        
        # 获取项目的所有视频合成队列
        from app.models.video_synthesis_queue import VideoSynthesisQueue
        queues = VideoSynthesisQueue.get_by_project(project_id)
        
        # 计算统计信息
        total_queues = len(queues)
        completed_count = len([q for q in queues if q.status == VideoSynthesisQueue.STATUS_COMPLETED])
        pending_count = len([q for q in queues if q.status == VideoSynthesisQueue.STATUS_PENDING])
        synthesizing_count = len([q for q in queues if q.status == VideoSynthesisQueue.STATUS_SYNTHESIZING])
        total_duration = sum(q.total_duration for q in queues)
        
        # 计算每个队列的已完成片段数和总片段数
        from app.models.temp_video_segment import TempVideoSegment
        queues_with_progress = []
        for queue in queues:
            # 计算该队列的已合成片段数
            # 注意：根据流程，合成完毁后片段状态可以是
            # STATUS_SYNTHESIZED（已合成）、STATUS_MERGED（已合并）、STATUS_DELETED（已删除）
            completed_segments = 0
            for temp_segment_id in queue.temp_segment_ids:
                temp_seg = TempVideoSegment.get_by_id(temp_segment_id)
                if temp_seg and temp_seg.status in [TempVideoSegment.STATUS_SYNTHESIZED, 
                                                     TempVideoSegment.STATUS_MERGED, 
                                                     TempVideoSegment.STATUS_DELETED]:
                    completed_segments += 1
            
            queues_with_progress.append({
                'queue': queue,
                'completed_segments': completed_segments,
                'total_segments': len(queue.temp_segment_ids)
            })
        
        return render_template(
            'video_queue.html',
            project=project,
            queues=queues_with_progress,
            total_queues=total_queues,
            completed_count=completed_count,
            pending_count=pending_count,
            synthesizing_count=synthesizing_count,
            total_duration=total_duration
        )
    except Exception as e:
        logger.error(f'查看视频队列失败: {str(e)}', exc_info=True)
        flash(f'查看视频队列失败: {str(e)}', 'error')
        return redirect(url_for('project.detail', project_id=project_id))


@project_bp.route('/<int:project_id>/video-preview')
def video_preview(project_id):
    """获取视频生成预览信息（API）"""
    try:
        project = ProjectService.get_project(project_id)
        if not project:
            return jsonify({'success': False, 'error': '项目不存在'}), 404
        
        # 获取项目配置
        config = project.config if isinstance(project.config, dict) else {}
        segment_duration = config.get('segment_duration', 600)
        
        # 获取已完成的音频段落
        from app.models.text_segment import TextSegment
        completed_segments = TextSegment.get_completed_segments(project_id)
        
        # 获取已有的视频队列
        from app.models.video_synthesis_queue import VideoSynthesisQueue
        existing_queues = VideoSynthesisQueue.get_by_project(project_id)
        
        if existing_queues:
            # 如果已有队列，返回实际数据
            total_queue_count = len(existing_queues)
            total_duration = sum(q.total_duration for q in existing_queues)
            estimated_time_minutes = int(total_duration / 60) if total_duration else 0
            estimated_disk_space_mb = int(total_queue_count * 100)  # 粗略估计每个视频100MB
            message = '已有生成队列数据，显示实际数据'
        else:
            # 若无队列，根据音频计算预估值
            if not completed_segments:
                return jsonify({
                    'success': True,
                    'data': {
                        'total_queue_count': 0,
                        'total_duration': 0,
                        'segment_duration': segment_duration,
                        'estimated_time_minutes': 0,
                        'estimated_disk_space_mb': 0,
                        'preview_available': False
                    }
                })
            
            # 计算预估队列数
            total_audio_duration = sum(s.audio_duration for s in completed_segments if s.audio_duration)
            total_queue_count = max(1, int(total_audio_duration / segment_duration))
            estimated_time_minutes = int(total_audio_duration / 60) if total_audio_duration else 0
            estimated_disk_space_mb = int(total_queue_count * 100)  # 粗略估计每个视频100MB
            message = '预估数据，基于已完成的音频段落计算'
        
        return jsonify({
            'success': True,
            'data': {
                'total_queue_count': total_queue_count,
                'total_duration': total_duration if existing_queues else (sum(s.audio_duration for s in completed_segments if s.audio_duration) or 0),
                'segment_duration': segment_duration,
                'estimated_time_minutes': estimated_time_minutes,
                'estimated_disk_space_mb': estimated_disk_space_mb,
                'preview_available': True,
                'message': message
            }
        })
    except Exception as e:
        logger.error(f'获取视频预览失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'获取预览失败: {str(e)}'}), 500
