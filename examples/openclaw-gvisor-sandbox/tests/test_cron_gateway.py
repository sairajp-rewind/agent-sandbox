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

"""Group 4 tests: Cron Gateway integrations (skip stubs)."""

import pytest


@pytest.mark.skip(
    reason=(
        "OpenClaw v2026.3.23 does not expose /v1/cron/next; endpoint is planned "
        "per https://github.com/tomergee/agent-sandbox/blob/openclaw-integration/"
        "plan/openclaw_idle_and_wake.md#2-inner-cron-integration--dynamic-pre-wakeup-jobs. "
        "Un-skip once the OpenClaw image with this endpoint ships."
    ),
)
def test_cron_next_returns_iso8601_timestamp_of_next_scheduled_run():
    """Verify cron gateway correctly reports next execution time."""
    pass


@pytest.mark.skip(
    reason=(
        "External Postgres/Spanner backend for cron/memory tables is a future "
        "architecture per https://github.com/tomergee/agent-sandbox/blob/"
        "openclaw-integration/plan/massive_scaling_openclaws.md. Un-skip once "
        "OpenClaw supports pointing SQLite-backed stores at an external DB."
    ),
)
def test_external_db_backend_reads_cron_from_postgres_not_local_sqlite():
    """Verify OpenClaw reads and writes cron records to external DB instead of local sqlite."""
    pass
