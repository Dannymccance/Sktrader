"""Discord channel preflight for DK Capital reporting.

GET access check by default. --send-test sends explicitly marked test messages
to verify the bot can publish; no trading or market API access.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import stat
import urllib.request


def preflight(token: str, channels: dict[str,str], *, send_test=False):
    outcomes={}
    for name,channel in channels.items():
        if not channel.isascii() or not channel.isdigit() or not 15<=len(channel)<=22:
            raise ValueError(f"invalid channel ID for {name}")
        headers={"Authorization":"Bot "+token,"User-Agent":"dkcapital-reporting-preflight/1.0"}
        req=urllib.request.Request(f"https://discord.com/api/v10/channels/{channel}",
                                   headers=headers,method="GET")
        with urllib.request.urlopen(req,timeout=12) as response:
            info=json.load(response)
        if str(info.get("id"))!=channel:
            raise RuntimeError(f"channel lookup mismatch for {name}")
        outcomes[name]={"visible":True,"channel_id":channel}
        if send_test:
            body={"content":f"[DK Capital reporting connectivity test] {name}: no trade was placed or filled.",
                  "allowed_mentions":{"parse":[]}}
            req=urllib.request.Request(
                f"https://discord.com/api/v10/channels/{channel}/messages",
                headers={**headers,"Content-Type":"application/json"},
                data=json.dumps(body).encode(),method="POST")
            with urllib.request.urlopen(req,timeout=12) as response:
                message=json.load(response)
            if not str(message.get("id","")).isdigit():
                raise RuntimeError(f"Discord did not acknowledge {name} message")
            outcomes[name]["test_message_id"]=str(message["id"])
    return outcomes


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--token-file",type=Path,required=True)
    p.add_argument("--channel",action="append",required=True,
                   help="one entry NAME=CHANNEL_ID; repeat to test every destination")
    p.add_argument("--send-test",action="store_true",
                   help="post a plainly labelled test message to each channel")
    args=p.parse_args()
    mode=args.token_file.stat().st_mode
    if mode & (stat.S_IRWXG|stat.S_IRWXO):
        p.error("bot token file must be owner-only")
    token=args.token_file.read_text().strip()
    channels={}
    for item in args.channel:
        name,sep,channel=item.partition("=")
        if not sep or not name or name in channels:
            p.error("channel must be a unique NAME=ID")
        channels[name]=channel
    print(json.dumps(preflight(token,channels,send_test=args.send_test),indent=2))


if __name__=="__main__":
    main()
