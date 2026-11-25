-- =====================================================================
-- Evidence-Gap Investigator - database schema (milestone 1)
--
-- Creates the `evidence_gap` database with two tables:
--   incidents  - one row per incident
--   evidence   - observations, negative findings and data gaps per incident
--
-- WARNING: this script DROPS and recreates both tables. Running it again
-- deletes all incident and evidence data. Re-run seed.sql afterwards.
--
-- Requires MySQL 8.0.16+ (older versions parse CHECK constraints but do
-- not enforce them).
--
-- Time convention: every DATETIME column stores UTC. DATETIME has no time
-- zone of its own, so the application must always write and read UTC.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS evidence_gap
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE evidence_gap;

SET time_zone = '+00:00';

DROP TABLE IF EXISTS evidence;
DROP TABLE IF EXISTS incidents;

-- ---------------------------------------------------------------------
-- incidents
-- ---------------------------------------------------------------------
CREATE TABLE incidents (
  id                  INT           NOT NULL AUTO_INCREMENT,
  incident_key        VARCHAR(20)   NOT NULL,              -- e.g. 'INC-1003'
  title               VARCHAR(200)  NOT NULL,
  summary             TEXT          NOT NULL,
  service             VARCHAR(100)  NOT NULL,              -- e.g. 'checkout-api'
  severity            ENUM('SEV1','SEV2','SEV3','SEV4') NOT NULL,
  status              ENUM('open','investigating','resolved') NOT NULL,
  started_at          DATETIME(3)   NOT NULL,              -- UTC, impact began
  detected_at         DATETIME(3)   NOT NULL,              -- UTC, someone/something noticed
  resolved_at         DATETIME(3)   NULL,                  -- UTC
  root_cause          TEXT          NULL,                  -- confirmed cause, resolved incidents only
  root_cause_category VARCHAR(50)   NULL,                  -- e.g. 'config_change'

  PRIMARY KEY (id),
  UNIQUE KEY uq_incidents_key (incident_key),
  KEY idx_incidents_service (service),
  KEY idx_incidents_category (root_cause_category),

  CONSTRAINT chk_incidents_detected_after_start
    CHECK (detected_at >= started_at),
  CONSTRAINT chk_incidents_resolved_after_start
    CHECK (resolved_at IS NULL OR resolved_at >= started_at),
  -- Answer-leak guard: a root cause may only be stored once the incident
  -- is resolved. Open/investigating incidents can never carry the answer
  -- the agent is supposed to find.
  CONSTRAINT chk_incidents_root_cause_only_when_resolved
    CHECK (status = 'resolved'
           OR (root_cause IS NULL AND root_cause_category IS NULL)),
  CONSTRAINT chk_incidents_resolved_fields
    CHECK (status <> 'resolved' OR resolved_at IS NOT NULL)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- evidence
--
-- evidence_kind:
--   observation       Something was seen (a log line, a metric value...).
--   negative_finding  Someone looked at a source that was complete for the
--                     window and found nothing. Weakens a hypothesis but
--                     never proves it false.
--   data_gap          Data for the window is missing or incomplete.
--                     Supports no conclusion at all; it only tells the
--                     agent what to ask for.
--
-- Time columns (all UTC):
--   observed_at     Point evidence: the moment it happened.
--                   Window evidence: start of the window.
--   observed_until  NULL for point evidence; end of the window otherwise.
--                   Required for negative_finding and data_gap, because
--                   "nothing found" / "nothing recorded" is only meaningful
--                   for an explicit time range. Relative phrases such as
--                   "in the last 24 hours" are never stored in content;
--                   the window is always stored in these two columns.
--   collected_at    When the evidence was recorded in this system.
--
-- limitations: what this evidence can NOT show, even if it is accurate.
-- reliability: how trustworthy the record itself is.
--
-- ON DELETE CASCADE trade-off (accepted for the MVP):
--   + Deleting an incident removes its evidence automatically, so no
--     orphaned evidence rows are left behind.
--   - A single mistaken DELETE on incidents silently removes all of its
--     evidence too; there is no undo. Later, when investigation results
--     reference evidence rows, a cascade could also break historical
--     analyses. Before that milestone we should switch to RESTRICT and/or
--     soft deletes (e.g. an archived_at column).
-- ---------------------------------------------------------------------
CREATE TABLE evidence (
  id              INT           NOT NULL AUTO_INCREMENT,
  incident_id     INT           NOT NULL,
  evidence_kind   ENUM('observation','negative_finding','data_gap') NOT NULL,
  source_type     ENUM('log','metric','alert','deploy','config_change',
                       'ticket','human_report') NOT NULL,
  source_name     VARCHAR(100)  NOT NULL,              -- e.g. 'redis-server', 'jenkins'
  observed_at     DATETIME(3)   NOT NULL,              -- UTC
  observed_until  DATETIME(3)   NULL,                  -- UTC
  collected_at    DATETIME(3)   NOT NULL,              -- UTC
  content         TEXT          NOT NULL,
  limitations     TEXT          NULL,
  reliability     ENUM('high','medium','low') NOT NULL,

  PRIMARY KEY (id),
  KEY idx_evidence_incident_time (incident_id, observed_at),

  CONSTRAINT fk_evidence_incident
    FOREIGN KEY (incident_id) REFERENCES incidents (id)
    ON DELETE CASCADE,

  CONSTRAINT chk_evidence_window_order
    CHECK (observed_until IS NULL OR observed_until >= observed_at),
  CONSTRAINT chk_evidence_window_required
    CHECK (evidence_kind = 'observation' OR observed_until IS NOT NULL),
  CONSTRAINT chk_evidence_collected_after_observed
    CHECK (collected_at >= observed_at)
) ENGINE = InnoDB;
