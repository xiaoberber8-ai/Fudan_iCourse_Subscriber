# Local teach_agent export

Subscriber can expose completed transcript segments and PPT OCR as
`LectureInboxEnvelope v1` files without connecting to teach_agent's MySQL.

```bash
python3 scripts/pull_data_branch.py --output data/icourse.db --env-file .env
python3 scripts/export_teach_agent.py \
  --db data/icourse.db \
  --inbox /absolute/path/to/teach_agent/data/inbox \
  --student-id local-student
```

The pull command reads the existing Subscriber credentials from the local,
Git-ignored env file, fetches encrypted shards, and rebuilds SQLite locally.
No credential is placed on the command line. The exporter writes a hidden
temporary file, flushes it, and atomically renames it to JSON. Its
`source_revision` is a SHA-256 of canonical course content, so an unchanged
lecture is not delivered twice. Historical flat transcripts are exported as
one segment without fabricated timestamps and do not trigger ASR again.

Keep the inbox and decrypted database local. Do not commit course content.
