-- =====================================================================
-- Evidence-Gap Investigator - synthetic seed data (milestone 1)
--
-- 8 fictional IT incidents with 41 evidence rows. All data is synthetic.
-- All timestamps are UTC.
--
-- Run schema.sql first. This script is NOT idempotent: running it twice
-- fails on the unique incident_key, which prevents duplicate data.
-- To start over, run schema.sql again, then this file.
--
-- Answer-leak rule: open/investigating incidents never store a root
-- cause (enforced by a CHECK constraint in schema.sql). Expected answers
-- for evaluation live outside the database, in separate test fixtures.
-- Comments in this file describe facts only, not expected conclusions.
-- =====================================================================

USE evidence_gap;
SET time_zone = '+00:00';

START TRANSACTION;

-- ---------------------------------------------------------------------
-- Incidents
-- ---------------------------------------------------------------------
INSERT INTO incidents
  (incident_key, title, summary, service, severity, status,
   started_at, detected_at, resolved_at, root_cause, root_cause_category)
VALUES
-- Resolved (historical) incidents --------------------------------------
('INC-1001',
 'checkout-api 5xx spike after deploy',
 'Checkout requests failed with 5xx errors for about 30 minutes shortly after a release.',
 'checkout-api', 'SEV2', 'resolved',
 '2026-07-08 13:40:00.000', '2026-07-08 13:43:00.000', '2026-07-08 14:20:00.000',
 'Release v2.1.4 lowered DB_POOL_MAX from 40 to 8. The connection pool was exhausted under normal traffic. Rolling back restored service.',
 'config_change'),

('INC-1002',
 'auth-service login failures and forced logouts',
 'Users were logged out and could not log back in during the evening peak.',
 'auth-service', 'SEV2', 'resolved',
 '2026-07-21 19:05:00.000', '2026-07-21 19:09:00.000', '2026-07-21 19:55:00.000',
 'Redis reached maxmemory under the allkeys-lru policy and evicted session keys. Raising maxmemory stopped the evictions.',
 'resource_exhaustion'),

('INC-1005',
 'checkout-api 502 errors during release window',
 'Checkout returned 502 errors to about 15% of requests. A release happened a few minutes before the errors started.',
 'checkout-api', 'SEV2', 'resolved',
 '2026-08-12 10:15:00.000', '2026-08-12 10:17:00.000', '2026-08-12 11:05:00.000',
 'Regional outage at the external payment provider (payment-gw) caused upstream timeouts. The concurrent release v2.2.7 contained only static assets and was unrelated.',
 'third_party'),

('INC-1006',
 'auth-service intermittent login failures',
 'Roughly one in six login attempts failed for about an hour.',
 'auth-service', 'SEV3', 'resolved',
 '2026-08-27 14:00:00.000', '2026-08-27 14:25:00.000', '2026-08-27 15:10:00.000',
 'NTP synchronisation stopped on node auth-3. Its clock drifted +94 s, so freshly issued JWTs were rejected as not yet valid. Restarting chronyd fixed the drift.',
 'infrastructure'),

('INC-1007',
 'search-service crash',
 'search-service stopped responding on host search-1 during the night.',
 'search-service', 'SEV3', 'resolved',
 '2026-09-03 03:10:00.000', '2026-09-03 03:30:00.000', '2026-09-03 04:00:00.000',
 'The nightly logrotate job had been failing for several days. /var/log filled to 100% and search-service crashed on a log write.',
 'resource_exhaustion'),

-- Open / investigating incidents (no root cause stored) -----------------
('INC-1003',
 'checkout-api returning 502 errors',
 'Elevated 502 responses on checkout. Errors dropped after a rollback; root cause not yet confirmed.',
 'checkout-api', 'SEV2', 'investigating',
 '2026-10-13 11:03:00.000', '2026-10-13 11:05:00.000', NULL, NULL, NULL),

('INC-1004',
 'Intermittent login failures on auth-service',
 'Users report that logging in sometimes fails. Failures appear intermittent rather than total.',
 'auth-service', 'SEV2', 'open',
 '2026-10-14 06:10:00.000', '2026-10-14 06:12:00.000', NULL, NULL, NULL),

('INC-1008',
 'payments-worker jobs failing',
 'All payment processing jobs have been failing since shortly after 02:00 UTC.',
 'payments-worker', 'SEV2', 'open',
 '2026-10-15 02:00:03.000', '2026-10-15 02:07:00.000', NULL, NULL, NULL);

SET @inc1001 = (SELECT id FROM incidents WHERE incident_key = 'INC-1001');
SET @inc1002 = (SELECT id FROM incidents WHERE incident_key = 'INC-1002');
SET @inc1003 = (SELECT id FROM incidents WHERE incident_key = 'INC-1003');
SET @inc1004 = (SELECT id FROM incidents WHERE incident_key = 'INC-1004');
SET @inc1005 = (SELECT id FROM incidents WHERE incident_key = 'INC-1005');
SET @inc1006 = (SELECT id FROM incidents WHERE incident_key = 'INC-1006');
SET @inc1007 = (SELECT id FROM incidents WHERE incident_key = 'INC-1007');
SET @inc1008 = (SELECT id FROM incidents WHERE incident_key = 'INC-1008');

-- ---------------------------------------------------------------------
-- Evidence
-- Column order:
--   incident_id, evidence_kind, source_type, source_name,
--   observed_at, observed_until, collected_at,
--   content, limitations, reliability
-- Window evidence always uses observed_at (start) + observed_until (end);
-- content never uses relative phrases such as "in the last 24 hours".
-- ---------------------------------------------------------------------
INSERT INTO evidence
  (incident_id, evidence_kind, source_type, source_name,
   observed_at, observed_until, collected_at,
   content, limitations, reliability)
VALUES
-- INC-1001 --------------------------------------------------------------
(@inc1001, 'observation', 'deploy', 'jenkins',
 '2026-07-08 13:38:20.000', NULL, '2026-07-08 13:38:21.000',
 'checkout-api v2.1.4 deployed. Config diff: DB_POOL_MAX 40 -> 8.',
 NULL, 'high'),
(@inc1001, 'observation', 'log', 'checkout-api',
 '2026-07-08 13:40:05.000', '2026-07-08 14:12:00.000', '2026-07-08 14:15:00.000',
 '"Timeout acquiring connection from pool (8/8 in use)" logged 2,310 times.',
 NULL, 'high'),
(@inc1001, 'observation', 'metric', 'prometheus',
 '2026-07-08 13:45:00.000', NULL, '2026-07-08 13:45:15.000',
 'db-primary CPU 25%, active connections 31 (within normal range).',
 'Single sample of the primary database only.', 'high'),
(@inc1001, 'observation', 'deploy', 'jenkins',
 '2026-07-08 14:12:30.000', NULL, '2026-07-08 14:12:31.000',
 'Rolled back to v2.1.3. 5xx rate returned to baseline (0.2%) within 3 minutes.',
 NULL, 'high'),

-- INC-1002 --------------------------------------------------------------
(@inc1002, 'observation', 'ticket', 'helpdesk',
 '2026-07-21 19:08:00.000', NULL, '2026-07-21 19:10:00.000',
 'About 40 users report being logged out and unable to log back in.',
 'User-reported times are approximate.', 'medium'),
(@inc1002, 'observation', 'metric', 'redis-server',
 '2026-07-21 19:00:00.000', '2026-07-21 19:50:00.000', '2026-07-21 19:51:00.000',
 'used_memory at 99-100% of maxmemory for the whole window. evicted_keys rose from 0 to 182,000.',
 NULL, 'high'),
(@inc1002, 'observation', 'log', 'auth-service',
 '2026-07-21 19:06:00.000', '2026-07-21 19:50:00.000', '2026-07-21 19:51:00.000',
 '"session not found" logged 9,800 times.',
 NULL, 'high'),
(@inc1002, 'observation', 'config_change', 'redis-server',
 '2026-07-21 19:52:00.000', NULL, '2026-07-21 19:52:05.000',
 'maxmemory raised from 2 GB to 6 GB. "session not found" errors stopped within 2 minutes.',
 NULL, 'high'),

-- INC-1005 --------------------------------------------------------------
(@inc1005, 'observation', 'deploy', 'jenkins',
 '2026-08-12 10:13:45.000', NULL, '2026-08-12 10:13:46.000',
 'checkout-api v2.2.7 deployed. Diff contains only static assets (CSS, images); no config or backend code changes.',
 NULL, 'high'),
(@inc1005, 'observation', 'log', 'checkout-api',
 '2026-08-12 10:15:00.000', '2026-08-12 11:00:00.000', '2026-08-12 11:01:00.000',
 '"upstream payment-gw timeout after 10000 ms" logged 4,120 times. No connection pool or database errors in the window.',
 NULL, 'high'),
(@inc1005, 'observation', 'alert', 'statuspage-monitor',
 '2026-08-12 10:20:00.000', NULL, '2026-08-12 10:20:30.000',
 'payment-gw public status page reports "Major outage - EU region".',
 'Status page updates can lag the real start of an outage.', 'medium'),
(@inc1005, 'observation', 'deploy', 'jenkins',
 '2026-08-12 10:31:00.000', NULL, '2026-08-12 10:31:01.000',
 'Rolled back to v2.2.6. 502 rate unchanged at about 15% after the rollback.',
 NULL, 'high'),
(@inc1005, 'observation', 'alert', 'statuspage-monitor',
 '2026-08-12 11:02:00.000', NULL, '2026-08-12 11:04:30.000',
 'payment-gw status page changed to "Resolved". checkout-api 502 rate back to baseline at 11:04.',
 NULL, 'high'),

-- INC-1006 --------------------------------------------------------------
(@inc1006, 'observation', 'ticket', 'helpdesk',
 '2026-08-27 14:20:00.000', NULL, '2026-08-27 14:22:00.000',
 'Users report intermittent login failures, roughly 1 in 6 attempts.',
 'User-reported times are approximate.', 'medium'),
(@inc1006, 'observation', 'metric', 'load-balancer',
 '2026-08-27 14:00:00.000', '2026-08-27 15:00:00.000', '2026-08-27 15:01:00.000',
 '97% of auth-service 5xx responses came from backend auth-3. Other backends below 0.5% error rate.',
 NULL, 'high'),
(@inc1006, 'observation', 'log', 'auth-service',
 '2026-08-27 14:00:00.000', '2026-08-27 15:00:00.000', '2026-08-27 15:01:00.000',
 '"token not yet valid (nbf)" logged 2,450 times, all on auth-3.',
 NULL, 'high'),
(@inc1006, 'observation', 'metric', 'node-exporter',
 '2026-08-27 14:40:00.000', NULL, '2026-08-27 14:40:10.000',
 'auth-3 clock offset +94 s compared with NTP reference. chronyd process not running.',
 NULL, 'high'),

-- INC-1007 --------------------------------------------------------------
(@inc1007, 'observation', 'alert', 'node-exporter',
 '2026-09-03 03:12:00.000', NULL, '2026-09-03 03:12:05.000',
 'search-1: /var/log filesystem 100% full.',
 NULL, 'high'),
(@inc1007, 'observation', 'log', 'search-service',
 '2026-09-03 03:15:00.000', NULL, '2026-09-03 03:31:00.000',
 '"No space left on device" followed by process exit (code 1).',
 NULL, 'high'),
(@inc1007, 'observation', 'log', 'cron',
 '2026-08-31 00:00:00.000', '2026-09-03 00:00:00.000', '2026-09-03 03:40:00.000',
 'logrotate job exited with an error on every nightly run in this window (4 runs).',
 NULL, 'high'),

-- INC-1003 --------------------------------------------------------------
(@inc1003, 'observation', 'deploy', 'jenkins',
 '2026-10-13 11:02:10.000', NULL, '2026-10-13 11:02:11.000',
 'checkout-api v2.4.0 deployed. Config diff: DB_POOL_MAX 50 -> 10.',
 NULL, 'high'),
(@inc1003, 'observation', 'log', 'checkout-api',
 '2026-10-13 11:03:40.000', '2026-10-13 11:31:00.000', '2026-10-13 11:32:00.000',
 '"Timeout acquiring connection from pool (10/10 in use)" logged 1,240 times.',
 NULL, 'high'),
(@inc1003, 'observation', 'alert', 'prometheus',
 '2026-10-13 11:05:00.000', NULL, '2026-10-13 11:05:05.000',
 'checkout-api 5xx rate 18% (alert threshold 2%).',
 NULL, 'high'),
(@inc1003, 'observation', 'metric', 'prometheus',
 '2026-10-13 11:06:00.000', NULL, '2026-10-13 11:06:15.000',
 'db-primary CPU 22%, active connections 34 (within normal range).',
 'Single sample of the primary database only.', 'high'),
(@inc1003, 'negative_finding', 'log', 'checkout-api',
 '2026-10-13 11:00:00.000', '2026-10-13 11:35:00.000', '2026-10-13 11:40:00.000',
 'No "upstream payment-gw timeout" errors in checkout-api logs for this window.',
 'Covers checkout-api application logs only; the log store was complete for this window.', 'high'),
(@inc1003, 'observation', 'deploy', 'jenkins',
 '2026-10-13 11:31:00.000', NULL, '2026-10-13 11:31:01.000',
 'Rolled back to v2.3.9. 5xx rate dropped to 0.3% within 2 minutes.',
 NULL, 'high'),

-- INC-1004 --------------------------------------------------------------
(@inc1004, 'observation', 'ticket', 'helpdesk',
 '2026-10-14 06:10:00.000', NULL, '2026-10-14 06:14:00.000',
 '14 users report that logging in fails sometimes but works on retry.',
 'User-reported times are approximate.', 'medium'),
(@inc1004, 'observation', 'metric', 'load-balancer',
 '2026-10-14 06:10:00.000', '2026-10-14 06:40:00.000', '2026-10-14 06:41:00.000',
 'auth-service 5xx rate fluctuating around 8% for the whole window.',
 'Aggregated over all backends; does not show which pod or node returned the errors.', 'high'),
(@inc1004, 'observation', 'metric', 'auth-service',
 '2026-10-14 06:15:00.000', NULL, '2026-10-14 06:15:30.000',
 'Client-side Redis call latency p99 = 900 ms (usual value about 5 ms).',
 'Measured from auth-service. Includes network time, client connection-pool wait and Redis server time; cannot tell which part was slow.', 'medium'),
(@inc1004, 'observation', 'log', 'redis-server',
 '2026-10-14 06:20:00.000', NULL, '2026-10-14 06:21:00.000',
 'Redis SLOWLOG is empty.',
 'SLOWLOG only records commands whose server-side execution exceeded 10 ms. It does not capture network delay, time spent queued before execution, or connection setup. The log has a fixed length and could have been reset.', 'high'),
(@inc1004, 'observation', 'metric', 'redis-server',
 '2026-10-14 06:20:00.000', NULL, '2026-10-14 06:21:00.000',
 'used_memory = 41% of maxmemory.',
 'Single point-in-time sample; memory between 06:10 and 06:20 is unknown. evicted_keys was not recorded.', 'high'),
(@inc1004, 'data_gap', 'log', 'auth-service',
 '2026-10-14 06:12:00.000', '2026-10-14 06:35:00.000', '2026-10-14 07:05:00.000',
 'Log shipping from auth-service was delayed. When the delayed batch arrived at 07:05, entries for this window were missing from the log store.',
 'Absence of log lines in this window says nothing about what happened; the logs were not received.', 'high'),
(@inc1004, 'data_gap', 'metric', 'redis-server',
 '2026-10-14 06:00:00.000', '2026-10-14 06:40:00.000', '2026-10-14 07:10:00.000',
 'Redis LATENCY monitor was disabled (latency-monitor-threshold = 0). No server-side latency history exists for this window.',
 NULL, 'high'),
(@inc1004, 'negative_finding', 'deploy', 'jenkins',
 '2026-10-13 06:10:00.000', '2026-10-14 06:10:00.000', '2026-10-14 06:50:00.000',
 'No deployments to auth-service in this window.',
 'Covers Jenkins pipelines only; manual changes or changes to shared infrastructure are not included.', 'high'),
(@inc1004, 'observation', 'human_report', 'network-oncall',
 '2026-10-14 06:30:00.000', NULL, '2026-10-14 06:31:00.000',
 'Network on-call: "We are not aware of any network issues right now."',
 'Based on impression; no specific checks or measurements were named.', 'low'),

-- INC-1008 --------------------------------------------------------------
(@inc1008, 'observation', 'alert', 'node-exporter',
 '2026-10-15 01:20:00.000', '2026-10-15 02:30:00.000', '2026-10-15 02:30:10.000',
 'worker-2 disk usage 85% (warning threshold 80%). Warning active for the whole window.',
 NULL, 'high'),
(@inc1008, 'observation', 'metric', 'prometheus',
 '2026-10-15 01:50:00.000', '2026-10-15 02:30:00.000', '2026-10-15 02:30:10.000',
 'payments-worker job failure rate 0% until 02:00:00, then 100% from 02:00:03 onward.',
 NULL, 'high'),
(@inc1008, 'observation', 'log', 'payments-worker',
 '2026-10-15 02:00:03.000', '2026-10-15 02:30:00.000', '2026-10-15 02:31:00.000',
 'Every call to the payment provider API returns HTTP 401 Unauthorized (3,880 occurrences). No other error types.',
 NULL, 'high'),
(@inc1008, 'observation', 'config_change', 'vault',
 '2026-10-15 02:00:00.000', NULL, '2026-10-15 02:15:00.000',
 'Secret payments/provider-api-key (key id pp-live-key-03) has expires_at = 2026-10-15 02:00:00 UTC. No newer version of the secret exists.',
 NULL, 'high'),
(@inc1008, 'negative_finding', 'deploy', 'jenkins',
 '2026-10-15 00:00:00.000', '2026-10-15 02:20:00.000', '2026-10-15 02:20:00.000',
 'No deployments to payments-worker in this window.',
 'Covers Jenkins pipelines only; manual changes are not included.', 'high'),
(@inc1008, 'negative_finding', 'log', 'payments-worker',
 '2026-10-15 01:50:00.000', '2026-10-15 02:30:00.000', '2026-10-15 02:31:00.000',
 'No disk-related errors (ENOSPC, I/O errors) in payments-worker logs for this window.',
 'Covers application logs only; the log store was complete for this window.', 'high');

COMMIT;
