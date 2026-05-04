"""TTS语音转视频独立脚本

功能：将已归档的TTS语音合成为视频
用途：不通过Web面板，直接将已合成的TTS语音合成为视频

使用方式：
1. 准备好TTS语音文件（命名格式：segment_*.mp3）
2. 准备好背景图片（支持PNG、JPG等格式）
3. 运行脚本，按提示输入路径和参数
4. 脚本将自动按文件名排序识别语音，并合成输出最终视频
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


class TTSVideoSynthesizer:
    """TTS语音转视频合成器"""

    def __init__(self):
        self.audio_dir = None
        self.image_dir = None
        self.output_dir = None
        self.video_name = None
        self.segment_duration = 30
        self.audio_files = []
        self.temp_dir = None

    def _print_step(self, message):
        """打印步骤信息"""
        print(f"\n{'='*60}")
        print(f"  {message}")
        print(f"{'='*60}")

    def _print_info(self, message):
        """打印信息"""
        print(f"  ℹ {message}")

    def _print_success(self, message):
        """打印成功信息"""
        print(f"  ✓ {message}")

    def _print_error(self, message):
        """打印错误信息"""
        print(f"  ✗ {message}")

    def _print_warning(self, message):
        """打印警告信息"""
        print(f"  ⚠ {message}")

    def _print_progress(self, current, total, message=""):
        """打印进度"""
        percent = int(current / total * 100)
        bar_length = 30
        filled = int(bar_length * current / total)
        bar = '█' * filled + '░' * (bar_length - filled)
        print(f"\r  [{bar}] {percent}% {message}", end='', flush=True)
        if current == total:
            print()

    def get_user_input(self):
        """获取用户输入"""
        print("\n" + "="*60)
        print("  TTS语音转视频合成工具")
        print("="*60)

        # 输入TTS语音路径
        while True:
            audio_path = input("\n请输入TTS语音所在目录路径: ").strip().strip('"').strip("'")
            if not audio_path:
                self._print_error("路径不能为空")
                continue
            if not os.path.exists(audio_path):
                self._print_error(f"路径不存在: {audio_path}")
                continue
            if not os.path.isdir(audio_path):
                self._print_error(f"路径不是目录: {audio_path}")
                continue
            self.audio_dir = audio_path
            break

        # 扫描音频文件
        self._scan_audio_files()
        if not self.audio_files:
            self._print_error("目录中没有找到TTS语音文件（segment_*.mp3）")
            return False

        # 输入背景图片路径
        while True:
            image_path = input("\n请输入背景图片所在目录路径: ").strip().strip('"').strip("'")
            if not image_path:
                self._print_error("路径不能为空")
                continue
            if not os.path.exists(image_path):
                self._print_error(f"路径不存在: {image_path}")
                continue
            if os.path.isdir(image_path):
                # 如果是目录，查找第一张图片
                image_files = []
                for ext in ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG']:
                    image_files.extend(Path(image_path).glob(ext))
                if not image_files:
                    self._print_error(f"目录中没有找到图片文件")
                    continue
                self.image_dir = str(image_files[0])
                self._print_info(f"使用图片: {self.image_dir}")
            else:
                self.image_dir = image_path
            break

        # 输入输出路径
        while True:
            output_path = input("\n请输入输出目录路径: ").strip().strip('"').strip("'")
            if not output_path:
                self._print_error("路径不能为空")
                continue
            self.output_dir = output_path
            break

        # 输入视频时长
        while True:
            try:
                duration_input = input(f"\n请输入每个视频片段的时长（秒，默认30）: ").strip()
                if not duration_input:
                    self.segment_duration = 30
                    break
                duration = int(duration_input)
                if duration <= 0:
                    self._print_error("时长必须大于0")
                    continue
                if duration < 5:
                    self._print_warning("时长过短可能会影响视频质量")
                self.segment_duration = duration
                break
            except ValueError:
                self._print_error("请输入有效的数字")
                continue

        while True:
            video_name_input = input("\n请输入视频名称（用于输出文件名）: ").strip()
            if not video_name_input:
                self._print_error("视频名称不能为空")
                continue
            self.video_name = video_name_input
            break

        # 显示配置摘要
        print("\n" + "-"*60)
        print("  配置摘要:")
        print(f"  - TTS语音目录: {self.audio_dir}")
        print(f"  - 音频文件数量: {len(self.audio_files)}")
        print(f"  - 背景图片: {self.image_dir}")
        print(f"  - 输出目录: {self.output_dir}")
        print(f"  - 视频名称: {self.video_name}")
        print(f"  - 视频片段时长: {self.segment_duration}秒")
        print("-"*60)

        confirm = input("\n确认开始合成? (y/n): ").strip().lower()
        if confirm != 'y' and confirm != 'yes':
            print("已取消")
            return False

        return True

    def _scan_audio_files(self):
        """扫描音频文件并按文件名中的数字排序"""
        pattern = os.path.join(self.audio_dir, "segment_*.mp3")
        files = list(Path(self.audio_dir).glob("segment_*.mp3"))

        def extract_segment_number(filename):
            """从文件名中提取数字部分用于排序"""
            import re
            match = re.search(r'segment_(\d+)', filename.name)
            if match:
                return int(match.group(1))
            return 0

        files.sort(key=extract_segment_number)
        self.audio_files = [str(f) for f in files]

    def _ensure_dir(self, path):
        """确保目录存在"""
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)

    def _check_ffmpeg(self):
        """检查ffmpeg是否可用"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                self._print_success("FFmpeg已安装")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        self._print_error("FFmpeg未安装或不可用，请先安装FFmpeg")
        return False

    def _get_audio_duration(self, audio_file):
        """获取音频时长"""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_file],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception as e:
            self._print_warning(f"获取音频时长失败: {e}")
        return 0

    def _merge_audio_files(self, audio_files, output_file):
        """合并多个音频文件"""
        self._print_info(f"开始合并 {len(audio_files)} 个音频文件...")

        # 创建concat文件
        concat_file = os.path.join(self.temp_dir, 'audio_concat_list.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for audio_file in audio_files:
                f.write(f"file '{os.path.abspath(audio_file)}'\n")

        # 执行FFmpeg合并
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            '-y',
            output_file
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=3600
            )

            # 清理concat文件
            try:
                os.remove(concat_file)
            except:
                pass

            if result.returncode != 0:
                self._print_error(f"音频合并失败: {result.stderr}")
                return False

            self._print_success(f"音频合并完成: {output_file}")
            return True

        except subprocess.TimeoutExpired:
            self._print_error("音频合并超时")
            return False
        except Exception as e:
            self._print_error(f"音频合并异常: {e}")
            return False

    def _split_audio_by_duration(self, input_audio, output_dir, duration):
        """按固定时长分割音频"""
        self._print_info(f"开始按 {duration} 秒分割音频...")

        # 获取音频总时长
        total_duration = self._get_audio_duration(input_audio)
        if total_duration <= 0:
            self._print_error("无法获取音频总时长")
            return []

        self._print_info(f"音频总时长: {total_duration:.2f} 秒")

        audio_segments = []
        segment_index = 0
        current_start = 0.0

        while current_start < total_duration:
            segment_output = os.path.join(output_dir, f'audio_segment_{segment_index:03d}.mp3')

            cmd = [
                'ffmpeg',
                '-i', input_audio,
                '-ss', str(current_start),
                '-t', str(duration),
                '-c', 'copy',
                '-y',
                segment_output
            ]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=600
                )

                if result.returncode != 0:
                    self._print_warning(f"分割音频片段失败: segment_{segment_index}")
                    break

                # 检查文件是否有效
                if os.path.exists(segment_output) and os.path.getsize(segment_output) > 10240:
                    audio_segments.append(segment_output)
                    segment_index += 1
                    current_start += duration
                else:
                    self._print_info("音频分割完成")
                    break

            except Exception as e:
                self._print_warning(f"分割异常: {e}")
                break

        self._print_success(f"音频分割完成: 共 {len(audio_segments)} 个片段")
        return audio_segments

    def _generate_background_video(self, background_image, output_file, duration):
        """生成固定时长的背景视频"""
        self._print_info(f"生成背景视频: 时长={duration}秒")

        cmd = [
            'ffmpeg',
            '-loop', '1',
            '-r', '1',
            '-t', str(duration),
            '-f', 'image2',
            '-i', background_image,
            '-c:v', 'libx264',
            '-crf', '24',
            '-pix_fmt', 'yuv420p',
            '-y',
            output_file
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=600
            )

            if result.returncode != 0:
                self._print_error(f"生成背景视频失败: {result.stderr}")
                return False

            if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                self._print_error("背景视频生成失败，文件无效")
                return False

            self._print_success(f"背景视频生成完成")
            return True

        except subprocess.TimeoutExpired:
            self._print_error("生成背景视频超时")
            return False
        except Exception as e:
            self._print_error(f"生成背景视频异常: {e}")
            return False

    def _synthesize_final_video(self, temp_video, audio_segment, output_file):
        """将背景视频与音频合成最终视频"""
        cmd = [
            'ffmpeg',
            '-i', temp_video,
            '-i', audio_segment,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-y',
            output_file
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=600
            )

            if result.returncode != 0:
                self._print_error(f"合成最终视频失败: {result.stderr}")
                return False

            return True

        except Exception as e:
            self._print_error(f"合成视频异常: {e}")
            return False

    def _generate_default_background(self, output_file, width=1920, height=1080):
        """生成默认背景图片"""
        self._print_info("生成默认黑色背景...")

        try:
            image = Image.new('RGB', (width, height), color=0)
            draw = ImageDraw.Draw(image)

            # 尝试使用系统字体
            try:
                font = ImageFont.truetype("msyh.ttc", 60)
            except:
                try:
                    font = ImageFont.truetype("simhei.ttf", 60)
                except:
                    font = ImageFont.load_default()

            # 添加简单的装饰线
            line_y = height // 2
            draw.line([(100, line_y), (width-100, line_y)], fill=(50, 50, 50), width=2)

            image.save(output_file)
            image.close()

            self._print_success("默认背景生成完成")
            return output_file

        except Exception as e:
            self._print_error(f"生成默认背景失败: {e}")
            return None

    def synthesize(self):
        """执行视频合成"""
        # 检查FFmpeg
        if not self._check_ffmpeg():
            return False

        # 创建临时目录
        self.temp_dir = os.path.join(self.output_dir, '_temp_tts_to_video')
        self._ensure_dir(self.temp_dir)
        self._ensure_dir(self.output_dir)

        self._print_step("步骤1: 合并TTS音频")

        # 合并所有音频
        long_audio_file = os.path.join(self.temp_dir, 'long_audio_all.mp3')
        if not self._merge_audio_files(self.audio_files, long_audio_file):
            return False

        self._print_step("步骤2: 分割长音频为固定时长片段")

        # 分割音频
        audio_segments = self._split_audio_by_duration(
            long_audio_file,
            self.temp_dir,
            self.segment_duration
        )

        if not audio_segments:
            self._print_error("音频分割失败")
            return False

        self._print_step("步骤3: 生成背景视频")

        # 准备背景图片
        background_image = self.image_dir
        if not os.path.exists(background_image):
            self._print_warning("背景图片不存在，生成默认背景")
            background_image = os.path.join(self.temp_dir, 'background.png')
            if not self._generate_default_background(background_image):
                return False

        # 生成固定时长的背景视频
        temp_video_file = os.path.join(self.temp_dir, 'temp_video_fixed.mp4')
        if not self._generate_background_video(background_image, temp_video_file, self.segment_duration):
            return False

        self._print_step("步骤4: 合成最终视频")

        # 合成最终视频
        total_segments = len(audio_segments)
        success_count = 0

        for idx, audio_segment in enumerate(audio_segments, 1):
            output_video_file = os.path.join(self.output_dir, f'{self.video_name}_{idx:03d}.mp4')

            self._print_progress(idx, total_segments, f"处理 {idx}/{total_segments}")

            if self._synthesize_final_video(temp_video_file, audio_segment, output_video_file):
                success_count += 1
            else:
                self._print_warning(f"视频片段 {idx} 合成失败")

        self._print_step("清理临时文件")

        # 清理临时目录
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                self._print_success("临时文件已清理")
        except Exception as e:
            self._print_warning(f"清理临时文件失败: {e}")

        self._print_step("合成完成")

        if success_count == total_segments:
            self._print_success(f"所有视频合成完成！共 {success_count} 个视频")
            self._print_info(f"输出目录: {self.output_dir}")
            return True
        else:
            self._print_warning(f"部分视频合成完成: 成功 {success_count}/{total_segments}")
            self._print_info(f"输出目录: {self.output_dir}")
            return success_count > 0


def main():
    """主函数"""
    synthesizer = TTSVideoSynthesizer()

    # 获取用户输入
    if not synthesizer.get_user_input():
        return

    # 执行合成
    try:
        success = synthesizer.synthesize()
        if success:
            print("\n" + "="*60)
            print("  ✓ 视频合成任务完成!")
            print("="*60)
            sys.exit(0)
        else:
            print("\n" + "="*60)
            print("  ✗ 视频合成任务失败")
            print("="*60)
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
        sys.exit(130)
    except Exception as e:
        print(f"\n发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
