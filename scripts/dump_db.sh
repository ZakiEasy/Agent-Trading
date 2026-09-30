#!/bin/bash
docker run --rm -v "$PWD":/workspace -w /workspace -e PGPASSWORD="LAk4UUk@Tfs@9qC" postgres:16 pg_dump -h aws-0-eu-central-1.pooler.supabase.com -p 6543 -U postgres.wszevrqncyusohdmiswe -d postgres --schema=public -F p -f /workspace/supabase_backup.sql
