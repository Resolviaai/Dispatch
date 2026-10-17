"""Fetch and display a fresh 5-minute Dispatch phone pairing code."""
import json
import urllib.request
import urllib.error
import sys

def main():
    url = "http://127.0.0.1:8000/api/integrations/youtube/pairing-code"
    data = json.dumps({"understands_http_risk": True}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Host": "127.0.0.1:8000", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            code = result.get("code")
            print("=" * 45)
            print(f"  DISPATCH PAIRING CODE:  {code}")
            print(f"  Valid for: 5 minutes")
            print("=" * 45)
    except urllib.error.HTTPError as e:
        print(f"Error ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to connect to Dispatch server: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
