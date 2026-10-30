import * as Effect from "effect/Effect";
import * as SqlClient from "effect/sql/SqlClient";

export default Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient;
  yield* sql`
    CREATE TABLE usage_turns (
      turn_id TEXT PRIMARY KEY,
      thread_id TEXT NOT NULL,
      recorded_at TEXT NOT NULL,
      payload_json TEXT NOT NULL
    ) WITHOUT ROWID
  `;
  yield* sql`CREATE INDEX usage_turns_thread_date_idx ON usage_turns(thread_id, recorded_at)`;
});
