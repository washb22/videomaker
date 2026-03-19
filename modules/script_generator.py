import os, json, re
import anthropic

TEMP = 'temp'

def generate_script(session_id: str, topic: str, custom_prompt: str = '') -> dict:
    """벤치 분석 결과를 바탕으로 대본 생성"""
    session_dir = os.path.join(TEMP, session_id)
    analysis_path = os.path.join(session_dir, 'analysis.json')

    with open(analysis_path, 'r', encoding='utf-8') as f:
        analysis = json.load(f)

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

    hooking = '\n'.join(analysis.get('hooking_patterns', []))
    tone = analysis.get('tone', '')
    style = analysis.get('style', '')
    formula = analysis.get('formula', '')

    prompt = f"""당신은 유튜브 대본 전문가입니다.
벤치마킹 채널 분석 결과를 바탕으로 아래 주제의 유튜브 영상 대본을 작성하세요.

[벤치 분석 결과]
- 후킹 패턴: {hooking}
- 말투/톤: {tone}
- 영상 결: {style}
- 핵심 공식: {formula}

[영상 주제]
{topic}

{f'[추가 지시사항]{chr(10)}{custom_prompt}' if custom_prompt else ''}

대본 작성 규칙:
1. 벤치 채널의 말투와 결의 '느낌'만 참고하되, 아래 금지 표현은 절대 사용하지 말 것
2. 첫 5초 안에 강력한 후킹으로 시작
3. 기승전결 구조 유지
4. 나레이션 형식으로 작성 (TTS로 읽힘)
5. 반드시 아래 형식으로 장면을 구분할 것:

[금지 표현 - 절대 사용 금지]
- 특정 채널의 인사말/호칭 복사 금지: "영순이 여러분", "야 여러분", "자 여러분" 등
- "야," 로 시작하는 문장 금지
- "자," 로 시작하는 문장 금지 (단, "자, 정리하면" 같은 전환 표현은 허용)
- 시청자를 "여러분"으로 부르는 것 최소화 (영상 전체에서 1~2회 이내)
- 구독/좋아요 CTA는 마지막 장면에서만 짧게
- 벤치 채널의 고유 표현이나 캐치프레이즈를 그대로 가져오지 말 것
- 대신 자연스러운 설명체 나레이션으로 작성 (담백하고 몰입감 있게)

[장면1]
나레이션: (여기에 읽힐 내용을 완전한 문장으로 작성)
이미지: (여기에 이미지 설명)

[장면2]
나레이션: (여기에 읽힐 내용을 완전한 문장으로 작성)
이미지: (여기에 이미지 설명)

6. 각 장면의 나레이션은 최소 3~5문장 이상으로 충분히 작성
7. 전체 장면 수: 10~15개
8. 나레이션과 이미지 설명을 반드시 분리해서 작성할 것

대본만 작성하세요."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}]
    )

    script_text = message.content[0].text

    # 저장
    script_path = os.path.join(session_dir, 'script_draft.txt')
    final_path = os.path.join(session_dir, 'script_final.txt')
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script_text)
    with open(final_path, 'w', encoding='utf-8') as f:
        f.write(script_text)

    # 장면 파싱
    scenes = parse_scenes(script_text)

    # 장면 데이터 저장
    with open(os.path.join(session_dir, 'scenes.json'), 'w', encoding='utf-8') as f:
        json.dump(scenes, f, ensure_ascii=False, indent=2)

    return {
        'script': script_text,
        'scenes': scenes,
        'scene_count': len(scenes)
    }


def parse_scenes(script: str) -> list:
    """대본에서 장면 파싱 - 나레이션/이미지 분리 형식 지원"""
    scenes = []

    # [장면N] 블록 분리
    pattern = r'\[장면\d+\](.*?)(?=\[장면\d+\]|$)'
    matches = re.findall(pattern, script, re.DOTALL)

    if matches:
        # 블록 내에 "나레이션:" 키워드가 있는지로 형식 판별
        has_narration_keyword = any(re.search(r'나레이션\s*:', m) for m in matches)

        for i, block in enumerate(matches):
            block = block.strip()

            if has_narration_keyword:
                # 형식 1: 나레이션: + 이미지: 분리 형식
                narration_match = re.search(r'나레이션\s*:\s*(.+?)(?=이미지\s*:|$)', block, re.DOTALL)
                image_match = re.search(r'이미지\s*:\s*(.+?)$', block, re.DOTALL)

                narration = narration_match.group(1).strip() if narration_match else ''
                image_desc = image_match.group(1).strip() if image_match else f"장면 {i+1} 이미지"
            else:
                # 형식 2: (이미지: 설명) 인라인 형식
                image_match = re.search(r'\(이미지:\s*(.+?)\)', block)
                image_desc = image_match.group(1).strip() if image_match else f"장면 {i+1}"
                # (이미지: ...) 부분만 제거하고 나머지가 나레이션
                narration = re.sub(r'\(이미지:\s*.+?\)', '', block).strip()

            # 나레이션 정리: 공백 통합 + 장면 라벨 제거 (후킹, 본격 전개 등)
            narration = re.sub(r'\s+', ' ', narration).strip()
            narration = re.sub(r'^(후킹|도입|전개|본격\s*전개|절정|클라이맥스|결말|마무리|엔딩|CTA)\s*', '', narration).strip()
            # 앞뒤 괄호/하이픈/구분선 정리
            narration = re.sub(r'^[-—─]+\s*', '', narration).strip()

            if narration and len(narration) > 10:
                scenes.append({
                    'index': i,
                    'narration': narration,
                    'image_description': image_desc
                })
        if scenes:
            return scenes

    # 형식 3: 장면 구분 없으면 단락으로 분리
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', script) if p.strip()]
    for i, para in enumerate(paragraphs):
        image_match = re.search(r'\(이미지:\s*(.+?)\)', para)
        image_desc = image_match.group(1).strip() if image_match else f"장면 {i+1}"
        narration = re.sub(r'\(이미지:\s*.+?\)', '', para).strip()
        narration = re.sub(r'\s+', ' ', narration).strip()
        if narration and len(narration) > 10:
            scenes.append({
                'index': i,
                'narration': narration,
                'image_description': image_desc
            })

    return scenes


def save_and_reparse(session_id: str, script_text: str) -> dict:
    """대본 저장 + scenes.json 재생성"""
    session_dir = os.path.join(TEMP, session_id)
    final_path = os.path.join(session_dir, 'script_final.txt')

    with open(final_path, 'w', encoding='utf-8') as f:
        f.write(script_text)

    scenes = parse_scenes(script_text)

    with open(os.path.join(session_dir, 'scenes.json'), 'w', encoding='utf-8') as f:
        json.dump(scenes, f, ensure_ascii=False, indent=2)

    return {'scenes': scenes, 'scene_count': len(scenes)}
