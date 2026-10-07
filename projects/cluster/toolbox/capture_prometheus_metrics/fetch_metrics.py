import argparse
import json
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--token-file", default=TOKEN_PATH)
parser.add_argument("--query", required=True)
parser.add_argument("--start", required=True, type=float)
parser.add_argument("--end", required=True, type=float)
parser.add_argument("--step", type=int, default=15)
parser.add_argument("--raw", action="store_true")
args = parser.parse_args()

with open(args.token_file) as f:
    token = f.read().strip()

headers = {"Authorization": f"Bearer {token}"}

if args.raw:
    duration = int(args.end - args.start)
    range_query = f"{args.query}[{duration}s]"
    response = requests.get(
        f"{args.url}/api/v1/query",
        params={"query": range_query, "time": args.end},
        headers=headers,
        verify=False,
    )
else:
    response = requests.get(
        f"{args.url}/api/v1/query_range",
        params={
            "query": args.query,
            "start": args.start,
            "end": args.end,
            "step": f"{args.step}s",
        },
        headers=headers,
        verify=False,
    )

response.raise_for_status()
json.dump(response.json(), sys.stdout)
