"""Stands in for unirig_job.py in tests; STUB_MODE picks the behavior."""

import argparse
import json
import os
import shutil
import sys
import time

parser = argparse.ArgumentParser()
for name in ("--unirig-dir", "--input", "--output", "--workdir", "--max-faces"):
    parser.add_argument(name)
args = parser.parse_args()
mode = os.environ.get("STUB_MODE", "ok")

if mode == "oom":
    print("ERROR gpu_out_of_memory CUDA out of memory", flush=True)
    sys.exit(1)
if mode == "crash":
    print("boom happened", file=sys.stderr, flush=True)
    sys.exit(3)
if mode == "sleep":
    time.sleep(30)
if mode == "env":
    keys = ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "HF_HOME", "HF_HUB_OFFLINE")
    with open(os.path.join(args.workdir, "env.json"), "w") as handle:
        json.dump({key: os.environ.get(key) for key in keys} | {"cwd": os.getcwd(), "args": vars(args)}, handle)

print("PROGRESS 0.4 Skeleton ready", flush=True)
shutil.copyfile(args.input, args.output)
print("RESULT 8 12", flush=True)
