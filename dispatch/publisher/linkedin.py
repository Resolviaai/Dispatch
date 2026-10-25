"""LinkedIn video publishing adapter using LinkedIn UGC API."""
import os
import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("dispatch.publisher.linkedin")


def upload_linkedin_video(
    video_path: Path,
    commentary: str,
    title: Optional[str] = None,
    access_token: Optional[str] = None,
    author_urn: Optional[str] = None
) -> Dict[str, Any]:
    """Publish video to LinkedIn personal profile or company page.
    
    Args:
        video_path: Path to local .mp4 video file.
        commentary: Post text commentary with hashtags.
        title: Optional video title.
        access_token: OAuth 2.0 access token with w_member_social or w_organization_social.
        author_urn: urn:li:person:XXXX or urn:li:organization:XXXX.
        
    Returns:
        Dict with status, remote_id, remote_url, is_simulation.
    """
    token = access_token or os.getenv("LINKEDIN_ACCESS_TOKEN")
    author = author_urn or os.getenv("LINKEDIN_AUTHOR_URN")

    if not token or not author:
        raise PermissionError("LinkedIn API credentials missing (LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN).")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    file_size = video_path.stat().st_size
    headers = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json"
    }

    try:
        logger.info("Registering LinkedIn video upload for %s (%d bytes)", author, file_size)

        # Primary: Modern LinkedIn Community Management API (Videos API & Posts API)
        init_url = "https://api.linkedin.com/rest/videos?action=initializeUpload"
        init_body = {
            "initializeUploadRequest": {
                "owner": author,
                "fileSizeBytes": file_size,
                "uploadCaptions": False,
                "uploadThumbnail": False
            }
        }
        init_res = requests.post(init_url, headers=headers, json=init_body, timeout=30)

        if init_res.status_code in (200, 201):
            val = init_res.json().get("value", {})
            video_urn = val.get("video")
            instructions = val.get("uploadInstructions", [])
            upload_url = instructions[0].get("uploadUrl") if instructions else None

            if video_urn and upload_url:
                logger.info("Uploading %d video bytes to LinkedIn Video CDN...", file_size)
                with open(video_path, "rb") as f:
                    up_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
                    up_res = requests.put(upload_url, headers=up_headers, data=f, timeout=180)
                    up_res.raise_for_status()

                logger.info("Creating LinkedIn post via Posts API...")
                post_url = "https://api.linkedin.com/rest/posts"
                post_body = {
                    "author": author,
                    "commentary": commentary,
                    "visibility": "PUBLIC",
                    "distribution": {
                        "feedDistribution": "MAIN_FEED",
                        "targetEntities": [],
                        "thirdPartyDistributionChannels": []
                    },
                    "content": {
                        "media": {
                            "title": title or commentary[:50],
                            "id": video_urn
                        }
                    },
                    "lifecycleState": "PUBLISHED",
                    "isReshareDisabledByAuthor": False
                }
                p_res = requests.post(post_url, headers=headers, json=post_body, timeout=30)
                p_res.raise_for_status()
                post_urn = p_res.headers.get("x-restli-id") or p_res.json().get("id", video_urn)
                remote_url = f"https://www.linkedin.com/feed/update/{post_urn}"

                logger.info("Successfully published video to LinkedIn: %s", remote_url)
                return {
                    "status": "published",
                    "remote_id": post_urn,
                    "remote_url": remote_url,
                    "is_simulation": False
                }

        # Fallback: Legacy Assets & ugcPosts API if modern endpoint is unavailable
        logger.info("Falling back to legacy LinkedIn assets/ugcPosts endpoint...")
        legacy_headers = {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json"
        }
        register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
        register_body = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
                "owner": author,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent"
                    }
                ]
            }
        }
        reg_res = requests.post(register_url, headers=legacy_headers, json=register_body, timeout=30)
        reg_res.raise_for_status()
        reg_data = reg_res.json().get("value", {})
        asset_urn = reg_data.get("asset")
        upload_mech = reg_data.get("uploadMechanism", {})
        upload_url = upload_mech.get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {}).get("uploadUrl")

        if not asset_urn or not upload_url:
            raise ValueError(f"LinkedIn upload registration failed: {reg_data}")

        # Upload Video Bytes
        logger.info("Uploading %d video bytes to LinkedIn CDN...", file_size)
        with open(video_path, "rb") as f:
            upload_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
            up_res = requests.post(upload_url, headers=upload_headers, data=f, timeout=180)
            up_res.raise_for_status()

        # Create UGC Post
        logger.info("Creating LinkedIn UGC video post...")
        post_url = "https://api.linkedin.com/v2/ugcPosts"
        post_body = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "media": [
                        {
                            "media": asset_urn,
                            "status": "READY",
                            "title": {"text": (title or commentary[:50])}
                        }
                    ],
                    "shareCommentary": {"text": commentary},
                    "shareMediaCategory": "VIDEO"
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }

        p_res = requests.post(post_url, headers=legacy_headers, json=post_body, timeout=30)
        p_res.raise_for_status()
        ugc_urn = p_res.json().get("id", asset_urn)
        remote_url = f"https://www.linkedin.com/feed/update/{ugc_urn}"

        logger.info("Successfully published video to LinkedIn: %s", remote_url)
        return {
            "status": "published",
            "remote_id": ugc_urn,
            "remote_url": remote_url,
            "is_simulation": False
        }

    except Exception as e:
        logger.error("LinkedIn publish failed: %s", e)
        raise
