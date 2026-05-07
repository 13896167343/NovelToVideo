"""
重新生成缺失的音频文件脚本

使用方法：
1. 确保程序已关闭或停止
2. 在项目根目录运行：python regenerate_missing_audio.py
3. 脚本会自动扫描指定项目，检查缺失的音频文件
4. 根据提示确认是否重新生成
5. 运行完毕后删除此脚本

支持指定项目ID：python regenerate_missing_audio.py 20
"""

import os
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app import create_app
from app.models.text_segment import TextSegment
from app.models.project import Project
from app.services.tts_service import TTSService
from config import DefaultConfig
from app.utils.database import execute_query


def get_all_projects():
    """获取所有项目"""
    query = 'SELECT id FROM projects'
    rows = execute_query(query)
    if not isinstance(rows, (list, tuple)):
        rows = []
    return rows


def get_segments_with_audio(project_id):
    """获取项目中所有应该有音频的段落（completed 或 failed 状态）"""
    query = '''
        SELECT * FROM text_segments 
        WHERE project_id = ? AND audio_status IN (?, ?)
        ORDER BY segment_index
    '''
    rows = execute_query(query, (
        project_id, 
        TextSegment.AUDIO_STATUS_COMPLETED,
        TextSegment.AUDIO_STATUS_FAILED
    ))
    if not isinstance(rows, (list, tuple)):
        rows = []
    return [TextSegment._from_row(row) for row in rows]


def check_audio_file_exists(segment, project_id):
    """检查音频文件是否存在"""
    audio_dir = os.path.join(DefaultConfig.TEMP_AUDIO_DIR, str(project_id))
    audio_file = os.path.join(audio_dir, f'segment_{segment.id}.mp3')
    return os.path.exists(audio_file), audio_file


def main():
    # 解析命令行参数
    target_project_id = None
    if len(sys.argv) > 1:
        try:
            target_project_id = int(sys.argv[1])
        except ValueError:
            print("错误：项目ID必须是数字")
            print("用法：python regenerate_missing_audio.py [project_id]")
            sys.exit(1)
    
    # 创建 Flask 应用上下文
    app = create_app()
    
    with app.app_context():
        print("=" * 60)
        print("音频文件重新生成工具")
        print("=" * 60)
        
        # 获取要处理的项目
        if target_project_id:
            projects = [(target_project_id,)]
            print(f"\n指定项目ID: {target_project_id}")
        else:
            projects = get_all_projects()
            print(f"\n扫描所有项目，共 {len(projects)} 个")
        
        total_missing = 0
        missing_files_info = []
        
        for project_row in projects:
            project_id = project_row[0]
            project = Project.get_by_id(project_id)
            project_name = project.name if project else f"项目{project_id}"
            
            # 获取应该有音频的段落
            segments = get_segments_with_audio(project_id)
            
            if not segments:
                if target_project_id:
                    print(f"\n项目 '{project_name}' (ID={project_id}) 没有需要音频的段落")
                continue
            
            project_missing = 0
            for segment in segments:
                exists, audio_file = check_audio_file_exists(segment, project_id)
                if not exists:
                    project_missing += 1
                    missing_files_info.append({
                        'project_id': project_id,
                        'project_name': project_name,
                        'segment_id': segment.id,
                        'audio_file': audio_file,
                        'content_preview': segment.content[:50] + '...' if len(segment.content) > 50 else segment.content
                    })
            
            if project_missing > 0:
                print(f"  项目 '{project_name}' (ID={project_id}): {project_missing} 个缺失文件")
                total_missing += project_missing
        
        print(f"\n总共发现 {total_missing} 个缺失的音频文件")
        
        if total_missing == 0:
            print("\n没有缺失的音频文件，无需重新生成")
            return
        
        # 显示缺失文件的详细信息（前20个）
        print("\n缺失文件列表（前20个）:")
        print("-" * 60)
        for i, info in enumerate(missing_files_info[:20]):
            print(f"{i+1}. 项目: {info['project_name']}")
            print(f"   段落ID: {info['segment_id']}")
            print(f"   路径: {info['audio_file']}")
            print(f"   内容: {info['content_preview']}")
            print()
        
        if len(missing_files_info) > 20:
            print(f"... 还有 {len(missing_files_info) - 20} 个文件未显示")
        
        # 确认是否继续
        print("\n" + "=" * 60)
        response = input(f"是否重新生成这 {total_missing} 个音频文件？(y/n): ").strip().lower()
        
        if response != 'y':
            print("已取消操作")
            return
        
        print("\n开始重新生成音频文件...")
        print("-" * 60)
        
        # 按项目分组处理
        projects_to_process = {}
        for info in missing_files_info:
            pid = info['project_id']
            if pid not in projects_to_process:
                projects_to_process[pid] = {
                    'name': info['project_name'],
                    'segments': []
                }
            projects_to_process[pid]['segments'].append(info['segment_id'])
        
        success_count = 0
        fail_count = 0
        
        for project_id, project_info in projects_to_process.items():
            print(f"\n处理项目: {project_info['name']} (ID={project_id})")
            
            # 重置这些段落的状态为 pending，以便重新生成
            for segment_id in project_info['segments']:
                TextSegment.update_audio_status(segment_id, TextSegment.AUDIO_STATUS_PENDING)
            
            # 调用 TTS 服务重新生成
            try:
                success, error = TTSService.synthesize_project(project_id)
                if success:
                    print(f"  ✓ 项目 {project_id} 音频生成成功")
                    success_count += len(project_info['segments'])
                else:
                    print(f"  ✗ 项目 {project_id} 音频生成失败: {error}")
                    fail_count += len(project_info['segments'])
            except Exception as e:
                print(f"  ✗ 项目 {project_id} 处理异常: {str(e)}")
                fail_count += len(project_info['segments'])
        
        print("\n" + "=" * 60)
        print(f"完成！成功: {success_count}, 失败: {fail_count}")
        print("=" * 60)
        
        if fail_count > 0:
            print("\n警告：有部分文件生成失败，请检查日志")


if __name__ == '__main__':
    main()
