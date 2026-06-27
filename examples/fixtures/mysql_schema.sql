-- ─────────────────────────────────────────────────────────────────────────────
-- CI schema + fixture data for ci-report (MySQL / MariaDB)
--
-- Load into MySQL / MariaDB:
--   mysql -u root < examples/fixtures/mysql_schema.sql
--
-- Tables
--   test_runs            → tool_mysql.py "build" config key (failures)
--   performance_metrics  → tool_mysql.py "models" config key (performance)
--
-- Fixture: 8 builds (1240-1247), 2 test scenarios, 2 performance models.
-- Build 1247 is the "current" build; build 1244 is the reference build.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE DATABASE IF NOT EXISTS ci_reports
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE ci_reports;

CREATE TABLE IF NOT EXISTS test_runs (
    id           BIGINT        AUTO_INCREMENT PRIMARY KEY,
    build        VARCHAR(50)   NOT NULL,
    scenario     VARCHAR(200)  NOT NULL,
    config       VARCHAR(200)  NOT NULL DEFAULT '',
    name         VARCHAR(500)  NOT NULL,
    status       ENUM('pass','fail','error','timeout','skip') NOT NULL,
    duration_s   FLOAT         NOT NULL DEFAULT 0,
    failure_msg  TEXT,
    failure_txt  TEXT,
    jira         VARCHAR(30)   DEFAULT '',
    jira_url     VARCHAR(500)  DEFAULT '',
    task_url     VARCHAR(500)  DEFAULT '',
    log_url      VARCHAR(500)  DEFAULT '',
    ran_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_build      (build),
    INDEX idx_build_name (build, name(191)),
    INDEX idx_name_ran   (name(191), ran_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─────────────────────────────────────────────────────────────────────────────
-- Scenario: E2E Upload Pipeline
-- ─────────────────────────────────────────────────────────────────────────────

-- Bearer token expiry at chunk boundary [PIPE-458]
-- Passes builds 1240-1246, fails in 1247 (first failure)
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',29.8,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1240/tasks/38','https://ci.example.com/jobs/1240/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',31.2,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1241/tasks/38','https://ci.example.com/jobs/1241/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',30.5,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1242/tasks/38','https://ci.example.com/jobs/1242/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',28.9,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1243/tasks/38','https://ci.example.com/jobs/1243/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',30.1,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1244/tasks/38','https://ci.example.com/jobs/1244/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',31.0,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1245/tasks/38','https://ci.example.com/jobs/1245/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',29.7,NULL,NULL,'PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1246/tasks/38','https://ci.example.com/jobs/1246/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','fail',18.1,'HTTP 403 Forbidden at chunk 7/8','HTTP 403 at chunk: 7/8\nbytes transferred: 56 MB of 64 MB\ntoken age at failure: 18m 02s\nhint: JFROG_CLI_TOKEN_REFRESH env var missing from agent-01\nsee: https://jfrog.com/help/r/jfrog-cli-documentation/environment-variables','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1247/tasks/38','https://ci.example.com/jobs/1247/logs/Bearer_token_expiry_at_chunk_boundary__PIPE_458_','2024-01-15 09:00:00');


-- Upload survives 60s network partition mid-transfer [PIPE-481]
-- Passes builds 1240-1246, times out in 1247 (first timeout)
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',34.2,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1240/tasks/39','https://ci.example.com/jobs/1240/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',35.8,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1241/tasks/39','https://ci.example.com/jobs/1241/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',33.7,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1242/tasks/39','https://ci.example.com/jobs/1242/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',36.1,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1243/tasks/39','https://ci.example.com/jobs/1243/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',34.5,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1244/tasks/39','https://ci.example.com/jobs/1244/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',35.3,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1245/tasks/39','https://ci.example.com/jobs/1245/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',33.9,NULL,NULL,'PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1246/tasks/39','https://ci.example.com/jobs/1246/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','timeout',192.0,'Test timed out after 3 minutes','TimeoutError: CLI reached MAX_RETRY_WAIT=120s without completing upload\n  CLI version: 2.52.0\n  File: archive-2024-01-15.tar.gz (1.2 GB)\n  Bytes uploaded before timeout: 1.1 GB (92%)\n  Regression: MAX_RETRY_WAIT was changed from 60s to 120s in CLI 2.52.0\n  Fix: set JFROG_CLI_UPLOAD_RETRY_WAIT_MAX=60 or downgrade CLI to 2.51.x','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1247/tasks/39','https://ci.example.com/jobs/1247/logs/Upload_survives_60s_network_partition_mid_transfer__PIPE_481_','2024-01-15 09:00:00');


-- Multipart checksum validation on reassembly
-- Passes all 8 builds
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, ran_at)
VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',11.2,'2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',12.4,'2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',10.8,'2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',13.1,'2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',11.9,'2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',12.6,'2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',11.4,'2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',12.2,'2024-01-15 09:00:00');


-- Retry after 503 Service Unavailable
-- Passes all 8 builds
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, ran_at)
VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.1,'2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',8.3,'2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',6.9,'2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.8,'2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',8.1,'2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.4,'2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',8.0,'2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.6,'2024-01-15 09:00:00');


-- ─────────────────────────────────────────────────────────────────────────────
-- Scenario: Checksum Deduplication
-- ─────────────────────────────────────────────────────────────────────────────

-- Cross-repo dedup timeout under concurrent load [PIPE-476]
-- Intermittent: fails on builds 1242 and 1247
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',15.2,NULL,NULL,'PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1240/tasks/44','https://ci.example.com/jobs/1240/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-08 09:05:00'),
('1241','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',14.8,NULL,NULL,'PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1241/tasks/44','https://ci.example.com/jobs/1241/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-09 09:05:00'),
('1242','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','fail',30.4,'DeduplicationTimeout: request exceeded 30s threshold','DeduplicationTimeout: sha256 dedup request took 30.4s, threshold=30s\n  context: 3 concurrent upload requests on k8s-pool-eu-west-1\n  artifact: libs-release-local/com/example/service/2.1.0/service-2.1.0.jar\n  hint: increase DEDUP_TIMEOUT_MS or reduce pod concurrency limit','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1242/tasks/44','https://ci.example.com/jobs/1242/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-10 09:05:00'),
('1243','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',16.1,NULL,NULL,'PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1243/tasks/44','https://ci.example.com/jobs/1243/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-11 09:05:00'),
('1244','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',15.5,NULL,NULL,'PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1244/tasks/44','https://ci.example.com/jobs/1244/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-12 09:05:00'),
('1245','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',15.9,NULL,NULL,'PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1245/tasks/44','https://ci.example.com/jobs/1245/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-13 09:05:00'),
('1246','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',14.7,NULL,NULL,'PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1246/tasks/44','https://ci.example.com/jobs/1246/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-14 09:05:00'),
('1247','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','fail',30.4,'DeduplicationTimeout: request exceeded 30s threshold','DeduplicationTimeout: sha256 dedup request took 30.4s, threshold=30s\n  context: 3 concurrent upload requests on k8s-pool-eu-west-1\n  artifact: libs-release-local/com/example/service/2.2.0/service-2.2.0.jar\n  hint: increase DEDUP_TIMEOUT_MS or reduce pod concurrency limit','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1247/tasks/44','https://ci.example.com/jobs/1247/logs/Cross_repo_dedup_timeout_under_concurrent_load__PIPE_476_','2024-01-15 09:05:00');


-- Dedup skips binary on SHA-256 edge case [PIPE-477]
-- Passes builds 1240-1246, errors in 1247 (first error)
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.1,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1240/tasks/45','https://ci.example.com/jobs/1240/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-08 09:05:00'),
('1241','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',0.9,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1241/tasks/45','https://ci.example.com/jobs/1241/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-09 09:05:00'),
('1242','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.0,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1242/tasks/45','https://ci.example.com/jobs/1242/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-10 09:05:00'),
('1243','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',0.8,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1243/tasks/45','https://ci.example.com/jobs/1243/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-11 09:05:00'),
('1244','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.2,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1244/tasks/45','https://ci.example.com/jobs/1244/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-12 09:05:00'),
('1245','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',0.9,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1245/tasks/45','https://ci.example.com/jobs/1245/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-13 09:05:00'),
('1246','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.0,NULL,NULL,'PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1246/tasks/45','https://ci.example.com/jobs/1246/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-14 09:05:00'),
('1247','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','error',0.2,'NullPointerException in DeduplicationService.resolveCollision()','java.lang.NullPointerException\n  at com.jfrog.artifactory.storage.DeduplicationService.resolveCollision(DeduplicationService.java:412)\n  at com.jfrog.artifactory.storage.DeduplicationService.processChunk(DeduplicationService.java:389)\n  at com.jfrog.artifactory.upload.UploadHandler.handleChunk(UploadHandler.java:201)\nCaused by: SHA-256 collision map entry was null for zero-padded digest\n  digest: 0000000000000000000000000000000000000000000000000000000000000001','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1247/tasks/45','https://ci.example.com/jobs/1247/logs/Dedup_skips_binary_on_SHA_256_edge_case__PIPE_477_','2024-01-15 09:05:00');


-- Dedup correctly handles zero-byte files
-- Passes all 8 builds
INSERT INTO test_runs
    (build, scenario, config, name, status, duration_s, ran_at)
VALUES
('1240','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-08 09:05:00'),
('1241','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.5,'2024-01-09 09:05:00'),
('1242','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.3,'2024-01-10 09:05:00'),
('1243','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-11 09:05:00'),
('1244','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.5,'2024-01-12 09:05:00'),
('1245','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-13 09:05:00'),
('1246','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.3,'2024-01-14 09:05:00'),
('1247','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-15 09:05:00');


-- ─────────────────────────────────────────────────────────────────────────────
-- Performance metrics table (queried when "models" key is set in config)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS performance_metrics (
    id           BIGINT        AUTO_INCREMENT PRIMARY KEY,
    build        VARCHAR(50)   NOT NULL,
    model        VARCHAR(200)  NOT NULL,
    metric_name  VARCHAR(200)  NOT NULL,
    unit         VARCHAR(50)   NOT NULL DEFAULT '',
    direction    ENUM('higher_better','lower_better') NOT NULL,
    value        DOUBLE        NOT NULL,
    recorded_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_model_build (model(100), build),
    INDEX idx_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Model: JFrog CLI 2.x · Linux arm64 — improving trend
INSERT INTO performance_metrics
    (build, model, metric_name, unit, direction, value, recorded_at)
VALUES
('1240','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',74.0,'2024-01-08 09:30:00'),
('1241','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',77.1,'2024-01-09 09:30:00'),
('1242','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',78.4,'2024-01-10 09:30:00'),
('1243','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',79.2,'2024-01-11 09:30:00'),
('1244','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',81.0,'2024-01-12 09:30:00'),
('1245','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',83.3,'2024-01-13 09:30:00'),
('1246','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',85.1,'2024-01-14 09:30:00'),
('1247','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',87.3,'2024-01-15 09:30:00'),
('1240','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',268,'2024-01-08 09:30:00'),
('1241','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',260,'2024-01-09 09:30:00'),
('1242','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',255,'2024-01-10 09:30:00'),
('1243','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',250,'2024-01-11 09:30:00'),
('1244','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',245,'2024-01-12 09:30:00'),
('1245','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',241,'2024-01-13 09:30:00'),
('1246','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',237,'2024-01-14 09:30:00'),
('1247','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',234,'2024-01-15 09:30:00'),
('1240','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',42.1,'2024-01-08 09:30:00'),
('1241','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',41.3,'2024-01-09 09:30:00'),
('1242','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',40.8,'2024-01-10 09:30:00'),
('1243','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',40.2,'2024-01-11 09:30:00'),
('1244','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',39.5,'2024-01-12 09:30:00'),
('1245','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',38.9,'2024-01-13 09:30:00'),
('1246','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',37.4,'2024-01-14 09:30:00'),
('1247','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',36.8,'2024-01-15 09:30:00');


-- Model: Curl Fallback · Linux amd64 — degrading trend
INSERT INTO performance_metrics
    (build, model, metric_name, unit, direction, value, recorded_at)
VALUES
('1240','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',76.2,'2024-01-08 09:30:00'),
('1241','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',75.8,'2024-01-09 09:30:00'),
('1242','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',75.1,'2024-01-10 09:30:00'),
('1243','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',74.9,'2024-01-11 09:30:00'),
('1244','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',74.3,'2024-01-12 09:30:00'),
('1245','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',73.7,'2024-01-13 09:30:00'),
('1246','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',72.8,'2024-01-14 09:30:00'),
('1247','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',72.1,'2024-01-15 09:30:00'),
('1240','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',280,'2024-01-08 09:30:00'),
('1241','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',285,'2024-01-09 09:30:00'),
('1242','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',290,'2024-01-10 09:30:00'),
('1243','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',295,'2024-01-11 09:30:00'),
('1244','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',300,'2024-01-12 09:30:00'),
('1245','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',305,'2024-01-13 09:30:00'),
('1246','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',308,'2024-01-14 09:30:00'),
('1247','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',312,'2024-01-15 09:30:00'),
('1240','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',72.4,'2024-01-08 09:30:00'),
('1241','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',71.1,'2024-01-09 09:30:00'),
('1242','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',70.3,'2024-01-10 09:30:00'),
('1243','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',68.9,'2024-01-11 09:30:00'),
('1244','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',66.7,'2024-01-12 09:30:00'),
('1245','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',64.2,'2024-01-13 09:30:00'),
('1246','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',62.5,'2024-01-14 09:30:00'),
('1247','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',61.1,'2024-01-15 09:30:00');
