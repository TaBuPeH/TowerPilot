/**
 * A2-7 timed actions (brief backend/logs/reviews/ui-pages/a2-7-actions-brief.md, R0): the schemas
 * of the battle_actions_projection, action_executions and action_executions_more families of the
 * landed r17b checkout pages fixture:
 * the battle read carrying `runtime.actions` (GET /api/battles/active), the battle detail carrying
 * `executions` rows (GET /api/battles/<id>) and the generic execution history
 * (GET /api/actions/executions).
 */
export {
  actionExecutionsAnswerSchema,
  actionExecutionsMoreAnswerSchema,
  actionsBattleDetailAnswerSchema,
  actionsMoreBattleDetailAnswerSchema,
  actionsProjectionReadAnswerSchema,
} from './generated/dashboard_contract_pages_v1_actions.86fbbe58.ts';
