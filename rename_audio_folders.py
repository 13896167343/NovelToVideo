"""重命名音频文件夹脚本

功能：将状态为"completed"的项目对应的 temp/audio 文件夹内的对应id的文件夹名称修改为项目的"name"

使用方式：直接运行脚本即可
"""
import os
import sqlite3
from pathlib import Path


def get_completed_projects():
    """从数据库获取所有已完成的项目"""
    db_path = Path(__file__).parent / 'data' / 'novel_to_video.db'
    
    if not db_path.exists():
        print(f"数据库文件不存在: {db_path}")
        return []
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name FROM projects WHERE status = 'completed'")
    projects = cursor.fetchall()
    
    conn.close()
    
    return [(row['id'], row['name']) for row in projects]


def rename_audio_folders():
    """重命名temp/audio文件夹中已完成项目的文件夹名称"""
    audio_dir = Path(__file__).parent / 'temp' / 'audio'
    
    if not audio_dir.exists():
        print(f"音频目录不存在: {audio_dir}")
        return
    
    completed_projects = get_completed_projects()
    
    if not completed_projects:
        print("没有找到已完成的项目")
        return
    
    print(f"\n找到 {len(completed_projects)} 个已完成的项目")
    print("-" * 50)
    
    renamed_count = 0
    skipped_count = 0
    
    for project_id, project_name in completed_projects:
        old_folder = audio_dir / str(project_id)
        new_folder = audio_dir / project_name
        
        if not old_folder.exists():
            print(f"跳过: 项目ID {project_id} ({project_name}) - 文件夹不存在")
            skipped_count += 1
            continue
        
        if old_folder == new_folder:
            print(f"跳过: 项目ID {project_id} ({project_name}) - 文件夹名称已正确")
            skipped_count += 1
            continue
        
        try:
            if new_folder.exists():
                print(f"跳过: 项目ID {project_id} ({project_name}) - 目标名称的文件夹已存在")
                skipped_count += 1
                continue
            
            os.rename(old_folder, new_folder)
            print(f"重命名: 项目ID {project_id} -> {project_name}")
            renamed_count += 1
        except Exception as e:
            print(f"错误: 项目ID {project_id} ({project_name}) - {str(e)}")
    
    print("-" * 50)
    print(f"完成! 共重命名 {renamed_count} 个文件夹，跳过 {skipped_count} 个")


if __name__ == '__main__':
    rename_audio_folders()