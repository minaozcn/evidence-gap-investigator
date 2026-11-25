-- =====================================================================
-- Evidence-Gap Investigator - follow-up evidence for INC-1004
--
-- NOT part of the initial seed. Run this only AFTER the agent has done a
-- first investigation of INC-1004, to simulate an engineer collecting
-- one of the requested pieces of evidence. Then re-run the agent and
-- compare the two investigations.
--
-- Safe to run more than once: the row is inserted only if it does not
-- already exist.
-- =====================================================================

USE evidence_gap;
SET time_zone = '+00:00';

SET @inc1004 = (SELECT id FROM incidents WHERE incident_key = 'INC-1004');

INSERT INTO evidence
  (incident_id, evidence_kind, source_type, source_name,
   observed_at, observed_until, collected_at,
   content, limitations, reliability)
SELECT
  @inc1004, 'observation', 'metric', 'load-balancer',
  '2026-10-14 06:10:00.000', '2026-10-14 06:40:00.000', '2026-10-14 09:30:00.000',
  'Per-backend breakdown: 95% of auth-service 5xx responses came from pod auth-7 (node k8s-node-12). The other 5 pods stayed below 0.5% error rate.',
  'Shows where the errors were returned, not why auth-7 failed. auth-7 was restarted at 06:41, so its local state from the incident window is lost.',
  'high'
FROM DUAL
WHERE @inc1004 IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM evidence
    WHERE incident_id = @inc1004
      AND source_name = 'load-balancer'
      AND collected_at = '2026-10-14 09:30:00.000'
  );

SELECT ROW_COUNT() AS rows_inserted;
