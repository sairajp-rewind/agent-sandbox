#!/usr/bin/env python3
# Copyright 2026 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import argparse
import time

try:
    from k8s_agent_sandbox.gke_extensions.snapshots import PodSnapshotSandboxClient
except ImportError:
    print("Error: k8s-agent-sandbox Python SDK is not installed in the current environment.")
    print("Please run this script inside the virtual environment where SDK is installed.")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Trigger GKE Pod Snapshot Suspend and Resume cycle for a SandboxClaim."
    )
    parser.add_argument(
        "claim_name",
        help="The name of the target SandboxClaim (e.g., openclaw-sandbox-claim)"
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Kubernetes namespace (default: default)"
    )
    args = parser.parse_args()

    print("Initializing PodSnapshotSandboxClient...")
    client = PodSnapshotSandboxClient()

    print(f"Resolving Sandbox handle for claim '{args.claim_name}' in namespace '{args.namespace}'...")
    try:
        sandbox = client.get_sandbox(claim_name=args.claim_name, namespace=args.namespace)
    except Exception as e:
        print(f"❌ Error resolving sandbox: {e}")
        sys.exit(1)

    print(f"Resolved Sandbox Name: {sandbox.sandbox_id}")
    try:
        pod_before = sandbox.get_pod_name()
        print(f"Active Pod before suspend: {pod_before}")
    except Exception:
        print("Active Pod before suspend: None")

    print("\n--- Phase 1: Triggering Snapshot Suspend ---")
    start_suspend = time.monotonic()
    
    suspend_res = sandbox.suspend(snapshot_before_suspend=True)
    if not suspend_res.success:
        print(f"❌ Suspend failed: {suspend_res.error_reason}")
        sys.exit(1)
        
    duration_suspend = time.monotonic() - start_suspend
    print(f"✅ Suspend succeeded! (Took {duration_suspend:.2f} seconds)")
    
    snap_uid = (
        suspend_res.snapshot_response.snapshot_uid 
        if suspend_res.snapshot_response 
        else "Unknown"
    )
    print(f"   Created PodSnapshot UID: {snap_uid}")

    print("\n--- Phase 2: Triggering Snapshot Resume ---")
    start_resume = time.monotonic()
    
    resume_res = sandbox.resume()
    if not resume_res.success:
        print(f"❌ Resume failed: {resume_res.error_reason}")
        sys.exit(1)
        
    duration_resume = time.monotonic() - start_resume
    print(f"✅ Resume succeeded! (Took {duration_resume:.2f} seconds)")
    print(f"   Restored from Snapshot UID: {resume_res.snapshot_uid}")
    
    try:
        pod_after = sandbox.get_pod_name()
        print(f"Active Pod after resume: {pod_after}")
    except Exception:
        pass

    print("\n--- Snapshot Cycle Completed Successfully ---")


if __name__ == "__main__":
    main()
