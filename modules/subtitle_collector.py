import subprocess, os, re, json

TEMP = 'temp'

def collect_subtitles(urls: list, session_id: str) -> dict:
    """유튜브 URL 리스트에서 자막 수집"""
    session_dir = os.path.join(TEMP, session_id)
    os.makedirs(session_dir, exist_ok=True)

    results = []
    for url in urls:
        try:
            # yt-dlp로 자막 다운로드
            cmd = [
                'python', '-m', 'yt_dlp',
                '--write-auto-sub',
                '--sub-lang', 'ko',
                '--skip-download',
                '--output', os.path.join(session_dir, '%(id)s.%(ext)s'),
                url
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            # 비디오 제목 추출
            title_cmd = [
                'python', '-m', 'yt_dlp',
                '--get-title',
                '--no-warnings',
                url
            ]
            title_proc = subprocess.run(title_cmd, capture_output=True, text=True, timeout=30)
            title = title_proc.stdout.strip() or url

            # vtt 파일 찾기
            vtt_files = [f for f in os.listdir(session_dir) if f.endswith('.vtt')]
            
            if vtt_files:
                latest_vtt = max(vtt_files, key=lambda f: os.path.getmtime(os.path.join(session_dir, f)))
                text = parse_vtt(os.path.join(session_dir, latest_vtt))
                results.append({
                    'url': url,
                    'title': title,
                    'text': text,
                    'success': True
                })
            else:
                results.append({'url': url, 'title': title, 'text': '', 'success': False, 'error': '자막 없음'})

        except Exception as e:
            results.append({'url': url, 'title': url, 'text': '', 'success': False, 'error': str(e)})

    # 수집 결과 저장
    with open(os.path.join(session_dir, 'bench_data.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return {
        'collected': len([r for r in results if r['success']]),
        'total': len(urls),
        'results': results
    }


def parse_vtt(path: str) -> str:
    """VTT 파일에서 순수 텍스트 추출"""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 태그 제거
    content = re.sub(r'<[^>]+>', '', content)
    # 타임스탬프 제거
    content = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}.*', '', content)
    # WEBVTT 헤더 제거
    content = re.sub(r'^WEBVTT.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^Kind:.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^Language:.*$', '', content, flags=re.MULTILINE)
    # 중복 줄 제거 및 정리
    lines = content.split('\n')
    seen = set()
    clean_lines = []
    for line in lines:
        line = line.strip()
        if line and line not in seen and not line.isdigit():
            seen.add(line)
            clean_lines.append(line)

    return ' '.join(clean_lines)
