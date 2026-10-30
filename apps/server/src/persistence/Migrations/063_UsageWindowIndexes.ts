import * as Effect from "effect/Effect";
import * as SqlClient from "effect/sql/SqlClient";

export default Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient;
  yield* sql`CREATE INDEX usage_turns_window_idx ON usage_turns(julianday(recorded_at))`;
  yield* sql`CREATE INDEX provider_turns_usage_window_idx
    ON orchestration_v2_projection_provider_turns(julianday(COALESCE(completed_at, started_at)))`;
  yield* sql`CREATE INDEX provider_turns_undated_usage_idx
    ON orchestration_v2_projection_provider_turns(thread_id)
    WHERE completed_at IS NULL AND started_at IS NULL`;
});
