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

import os
import time
import pytest
from dotenv import load_dotenv

from k8s_agent_sandbox.gke_extensions.snapshots.podsnapshot_client import (
    PodSnapshotSandboxClient,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")


@pytest.fixture(scope="session", autouse=True)
def load_env():
    """Load environment variables from .env prior to test execution."""
    if os.path.exists(ENV_FILE):
        load_dotenv(ENV_FILE)


@pytest.fixture(scope="session")
def env_vars(load_env):
    """Retrieve environment variables provided by pre-test.sh with fail-fast validation."""
    bucket = os.getenv("BUCKET_NAME")
    if not bucket:
        pytest.exit("BUCKET_NAME not in .env — did pre-test.sh run successfully?")

    return {
        "bucket_name": bucket,
        "tenant_single_ns": os.getenv("TENANT_SINGLE_NS", "tenant-single"),
        "tenant_alpha_ns": os.getenv("TENANT_ALPHA_NS", "tenant-alpha"),
        "tenant_beta_ns": os.getenv("TENANT_BETA_NS", "tenant-beta"),
        "warm_pool_name": os.getenv("WARM_POOL_NAME", "python-counter-pool"),
    }


@pytest.fixture(scope="module")
def single_user_client(env_vars):
    """Initialize PodSnapshotSandboxClient for single-user testing with automatic teardown."""
    client = PodSnapshotSandboxClient()
    yield client
    client.delete_all()


@pytest.fixture(scope="module")
def multi_user_client(single_user_client):
    """Alias for PodSnapshotSandboxClient in multi-tenant test contexts."""
    return single_user_client


@pytest.fixture
def wait_for_snapshot_ready():
    """Pytest fixture function to poll until a specific snapshot UID is reported as ready."""
    def _wait(sandbox, snapshot_uid: str, max_retries: int = 30, sleep_time: int = 2) -> bool:
        for _ in range(max_retries):
            check_list = sandbox.snapshots.list()
            if check_list.success and any(s.snapshot_uid == snapshot_uid for s in check_list.snapshots):
                return True
            time.sleep(sleep_time)
        return False
    return _wait
