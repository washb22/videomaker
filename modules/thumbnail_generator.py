import os, json, base64, requests
from openai import OpenAI

TEMP = 'temp'


def generate_thumbnail(session_id: str, title: str = '') -> dict:
    """조회수 높은 유튜브 썸네일을 레퍼런스 삼아 썸네일 생성"""
    session_dir = os.path.join(TEMP, session_id)
    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    # 1. 벤치마킹 영상의 썸네일 수집
    thumbnails = _collect_reference_thumbnails(session_dir)

    # 2. 대본에서 제목/주제 추출
    if not title:
        title = _extract_title(session_dir)

    # 3. 레퍼런스 썸네일 분석 (GPT 비전)
    analysis = _analyze_thumbnails(client, thumbnails, title)

    # 4. 썸네일 생성
    thumb_path = os.path.join(session_dir, 'thumbnail.png')
    result = _generate_thumb_image(client, analysis, title, thumb_path)

    return result


def _collect_reference_thumbnails(session_dir: str) -> list:
    """벤치마킹 영상들의 썸네일 URL 수집"""
    thumbnails = []

    # bench_data.json에서 영상 ID 추출
    bench_path = os.path.join(session_dir, 'bench_data.json')
    if os.path.exists(bench_path):
        with open(bench_path, 'r', encoding='utf-8') as f:
            bench = json.load(f)
        for item in bench:
            vid = item.get('video_id', '')
            if vid:
                # YouTube 고화질 썸네일 URL
                thumbnails.append({
                    'url': f'https://img.youtube.com/vi/{vid}/maxresdefault.jpg',
                    'fallback_url': f'https://img.youtube.com/vi/{vid}/hqdefault.jpg',
                    'title': item.get('title', '')
                })

    return thumbnails[:5]  # 최대 5개


def _extract_title(session_dir: str) -> str:
    """대본에서 제목/주제 추출"""
    script_path = os.path.join(session_dir, 'script_final.txt')
    if os.path.exists(script_path):
        with open(script_path, 'r', encoding='utf-8') as f:
            first_lines = f.read(500)
        # 첫 줄이나 # 제목 추출
        for line in first_lines.split('\n'):
            line = line.strip()
            if line and not line.startswith('---'):
                # # 제목 형식이면 # 제거
                if line.startswith('#'):
                    return line.lstrip('#').strip()
                return line
    return "유튜브 영상"


def _download_thumbnail(url: str, fallback_url: str = '') -> bytes:
    """썸네일 이미지 다운로드"""
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
    except:
        pass
    if fallback_url:
        try:
            r = requests.get(fallback_url, timeout=10)
            if r.status_code == 200:
                return r.content
        except:
            pass
    return None


def _analyze_thumbnails(client, thumbnails: list, title: str) -> str:
    """GPT 비전으로 레퍼런스 썸네일 분석"""

    # 썸네일 이미지 다운로드 + base64 인코딩
    image_contents = []
    for thumb in thumbnails:
        img_data = _download_thumbnail(thumb['url'], thumb.get('fallback_url', ''))
        if img_data:
            b64 = base64.b64encode(img_data).decode('utf-8')
            image_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "low"
                }
            })

    if not image_contents:
        return "밝은 색상, 큰 텍스트, 인물 클로즈업, 감정 표현이 강한 구도"

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""이 유튜브 썸네일들을 분석해주세요. 다음 항목을 정리해주세요:

1. **레이아웃 패턴**: 텍스트 위치(좌/우/중앙), 인물 위치, 배경 구성
2. **텍스트 스타일**: 글자 크기, 색상, 테두리/그림자, 줄 수
3. **색상 톤**: 주요 색상, 대비, 밝기
4. **감정/분위기**: 자극적인지, 궁금증 유발인지, 충격적인지
5. **공통 패턴**: 이 썸네일들이 공유하는 핵심 성공 요소

그리고 이 분석을 바탕으로, 아래 주제의 썸네일을 만들기 위한 구체적인 이미지 생성 프롬프트를 작성해주세요.
- 주제: {title}
- 프롬프트는 영어로, 레이아웃/구도/색상/텍스트 배치를 구체적으로 지시
- 유튜브 썸네일 규격 (1280x720)에 최적화

JSON 형식으로 응답:
{{"analysis": "분석 요약", "prompt": "영어 이미지 생성 프롬프트", "overlay_text": "썸네일에 들어갈 한글 텍스트 (10자 이내, 임팩트 있게)"}}"""
                },
                *image_contents
            ]
        }
    ]

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=messages,
            max_tokens=1500
        )
        text = response.choices[0].message.content

        # JSON 파싱
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"썸네일 분석 실패: {e}")

    return {
        "analysis": "분석 불가",
        "prompt": f"YouTube thumbnail for a video about {title}, dramatic lighting, bold large Korean text, emotional facial expression, high contrast colors, clickbait style, 1280x720",
        "overlay_text": title[:10]
    }


def _generate_thumb_image(client, analysis: dict, title: str, output_path: str) -> dict:
    """분석 결과 기반 썸네일 이미지 생성"""

    if isinstance(analysis, str):
        prompt = f"YouTube thumbnail, {analysis}, topic: {title}, 1280x720 ratio, bold text, dramatic"
        overlay_text = title[:10]
    else:
        prompt = analysis.get('prompt', '')
        overlay_text = analysis.get('overlay_text', title[:10])

    # gpt-image-1.5로 썸네일 생성
    try:
        response = client.images.generate(
            model="gpt-image-1.5",
            prompt=prompt,
            size="1536x1024",
            quality="high",
            n=1
        )

        img_obj = response.data[0]
        if hasattr(img_obj, 'b64_json') and img_obj.b64_json:
            img_data = base64.b64decode(img_obj.b64_json)
        elif hasattr(img_obj, 'url') and img_obj.url:
            img_data = requests.get(img_obj.url, timeout=30).content
        else:
            raise Exception("이미지 데이터 없음")

        with open(output_path, 'wb') as f:
            f.write(img_data)

            return {
            'success': True,
            'path': output_path,
            'analysis': analysis.get('analysis', '') if isinstance(analysis, dict) else ''
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def _add_text_overlay(input_path: str, output_path: str, text: str):
    """FFmpeg로 썸네일에 한글 텍스트 오버레이"""
    import subprocess

    # 텍스트가 비어있으면 그냥 복사
    if not text.strip():
        import shutil
        shutil.copy2(input_path, output_path)
        return

    try:
        # Windows/한글 호환 폰트 사용
        safe_input = input_path.replace('\\', '/').replace(':', '\\:')
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vf', (
                f"drawtext=text='{text}':"
                f"fontfile='C\\:/Windows/Fonts/malgunbd.ttf':"
                f"fontsize=80:"
                f"fontcolor=white:"
                f"borderw=5:"
                f"bordercolor=black:"
                f"x=(w-text_w)/2:"
                f"y=h-text_h-60"
            ),
            output_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)

        if not os.path.exists(output_path):
            import shutil
            shutil.copy2(input_path, output_path)
    except:
        import shutil
        shutil.copy2(input_path, output_path)
