import os, json
import anthropic

TEMP = 'temp'

def analyze_bench(session_id: str) -> dict:
    """수집된 자막을 Claude API로 분석 - 말투/후킹/결/주제 추출"""
    session_dir = os.path.join(TEMP, session_id)
    bench_path = os.path.join(session_dir, 'bench_data.json')

    with open(bench_path, 'r', encoding='utf-8') as f:
        bench_data = json.load(f)

    # 성공한 자막만 모음
    texts = []
    for item in bench_data:
        if item['success'] and item['text']:
            texts.append(f"[{item['title']}]\n{item['text'][:3000]}")

    if not texts:
        raise Exception("분석할 자막 데이터가 없습니다")

    combined = '\n\n---\n\n'.join(texts)

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

    prompt = f"""다음은 유튜브 벤치마킹 채널들의 영상 대본입니다.
이 채널들의 특징을 아래 항목별로 분석해주세요.

[벤치 대본들]
{combined}

분석 항목:
1. 후킹 패턴: 영상 시작 5~10초 안에 어떻게 시청자를 잡는지
2. 말투/톤: 어떤 말투를 사용하는지 (친근한/전문적/충격적/공감형 등)
3. 영상 결(스타일): 전체적인 구성 방식과 흐름
4. 주요 소재: 자주 다루는 주제 패턴
5. 추천 주제 10개: 이 채널 스타일로 만들면 잘 될 것 같은 구체적인 영상 주제
6. 핵심 공식: 조회수 나오는 이 채널만의 공식 한 줄 요약

JSON 형식으로만 응답하세요:
{{
  "hooking_patterns": ["패턴1", "패턴2", "패턴3"],
  "tone": "말투 설명",
  "style": "영상 결 설명",
  "main_topics": ["소재1", "소재2", "소재3"],
  "recommended_topics": ["주제1", "주제2", "주제3", "주제4", "주제5", "주제6", "주제7", "주제8", "주제9", "주제10"],
  "formula": "핵심 공식 한 줄"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = message.content[0].text
    # JSON 파싱
    try:
        # JSON 블록 추출
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
        else:
            analysis = json.loads(response_text)
    except:
        analysis = {"raw": response_text}

    # 저장
    with open(os.path.join(session_dir, 'analysis.json'), 'w', encoding='utf-8') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    return analysis
