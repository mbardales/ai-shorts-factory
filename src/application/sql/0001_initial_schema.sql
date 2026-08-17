-- 0001_initial_schema.sql (ME40.6)
-- Schema mínimo de metadata para PostgreSQLPersistenceRepository.
--
-- Solo se aplica al aprovisionar la base (migración versionada); el MVP
-- continúa usando LocalPersistenceRepository (JSON) sin tocar nada de esto.
-- NO se guardan assets (imágenes/audio/video): esos siguen viviendo en
-- RunStorage (filesystem). Aquí solo vive la metadata de runs y proyectos.
--
-- Orden de creación de un run (ApplicationService.create_run):
--   1) associate_run()  -> INSERT en run_projects (la fila del run aún NO
--      existe; por eso run_projects NO tiene FK hacia runs(run_id)).
--   2) save_run()       -> UPSERT en runs (proyecta project_id desde
--      run_projects como espejo denormalizado para consultas por SQL).

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    active      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    -- topic/offline: reservados para cuando la cola (job.json) migre a la
    -- base; el contrato PersistenceRepository aún no los puebla.
    topic          TEXT,
    offline        BOOLEAN,
    -- Espejo denormalizado de run_projects.project_id (ver comentario inicial).
    project_id     TEXT REFERENCES projects (id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ,
    queued_at      TIMESTAMPTZ,
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ,
    error          TEXT,
    quality_passed BOOLEAN
);

CREATE TABLE IF NOT EXISTS run_projects (
    run_id     TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);