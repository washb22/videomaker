import os
import requests

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

def search_videos(keyword: str, max_results: int = 10, order: str = "viewCount") -> dict:
    """키워드로 유튜브 영상 검색 - 조회수 높은 순"""
    api_key = os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        raise Exception("YOUTUBE_API_KEY가 없습니다")

    # 1. 영상 검색
    search_url = f"{YOUTUBE_API_BASE}/search"
    search_params = {
        "key": api_key,
        "q": keyword,
        "part": "snippet",
        "type": "video",
        "maxResults": max_results,
        "order": order,
        "regionCode": "KR",
        "relevanceLanguage": "ko"
    }

    search_res = requests.get(search_url, params=search_params, timeout=10)
    search_data = search_res.json()

    if "error" in search_data:
        raise Exception(f"YouTube API 오류: {search_data['error']['message']}")

    items = search_data.get("items", [])
    video_ids = [item["id"]["videoId"] for item in items]

    if not video_ids:
        return {"videos": [], "total": 0}

    # 2. 조회수/통계 가져오기
    stats_url = f"{YOUTUBE_API_BASE}/videos"
    stats_params = {
        "key": api_key,
        "id": ",".join(video_ids),
        "part": "statistics,contentDetails,snippet"
    }

    stats_res = requests.get(stats_url, params=stats_params, timeout=10)
    stats_data = stats_res.json()

    stats_map = {}
    for item in stats_data.get("items", []):
        vid = item["id"]
        stats = item.get("statistics", {})
        details = item.get("contentDetails", {})
        stats_map[vid] = {
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "duration": parse_duration(details.get("duration", "PT0S"))
        }

    # 3. 결과 조합
    videos = []
    for item in items:
        vid = item["id"]["videoId"]
        snippet = item["snippet"]
        stat = stats_map.get(vid, {})

        videos.append({
            "video_id": vid,
            "url": f"https://youtu.be/{vid}",
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
            "published": snippet.get("publishedAt", "")[:10],
            "view_count": stat.get("view_count", 0),
            "like_count": stat.get("like_count", 0),
            "comment_count": stat.get("comment_count", 0),
            "duration": stat.get("duration", ""),
            "view_count_str": format_count(stat.get("view_count", 0))
        })

    # 조회수 높은 순 정렬
    videos.sort(key=lambda x: x["view_count"], reverse=True)

    return {"videos": videos, "total": len(videos)}


def parse_duration(duration: str) -> str:
    """PT1H2M3S → 1:02:03"""
    import re
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return "0:00"
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_count(n: int) -> str:
    """1234567 → 123만"""
    if n >= 100000000:
        return f"{n//100000000}억"
    if n >= 10000:
        return f"{n//10000}만"
    if n >= 1000:
        return f"{n//1000}천"
    return str(n)
