-- Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
CREATE TABLE IF NOT EXISTS nexent.document_parser_settings (
    tenant_id varchar(100) NOT NULL,
    agent_id integer NOT NULL CHECK (agent_id >= 0),
    settings jsonb NOT NULL,
    updated_by varchar(100) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, agent_id)
);
