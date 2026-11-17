// persona 组件：好友证据化人格报告。
const CONV_ID = '^conv_[0-9a-f]{16}$';
const RUN_ID = '^run_[0-9a-z_]{8,60}$';

export default {
  id: 'persona',
  prompt: {
    name: 'tool:wechat-one-persona',
    order: 118,
    text: [
      '- 生成好友人格报告：先 wechat_one_contacts 确认好友 conversation_id，再 wechat_one_persona_prepare 取统计与采样素材，',
      '按返回的 schema 写语义稿（每节必须有 evidence 中的 msg_id 与逐字 quotes），再 wechat_one_persona_render 生成报告；',
      '校验失败时按 errors 逐条修正后重试一次。报告是基于聊天片段的印象，不是心理测评，不要给对方贴诊断性标签。',
    ].join(''),
  },
  tools: [
    {
      name: 'wechat_one_persona_prepare',
      action: 'persona_prepare',
      kind: 'read',
      description: '为一位私聊好友准备人格报告素材：本地计算统计（时段分布、主动开口比例、回复时延、消息长度与类型、口头禅候选、月度热度、情感倾向），'
        + '并按月分层采样对方发言及上下文（每条带 msg_id）。返回 run_id、统计摘要、素材与语义稿 schema。',
      parameters: {
        conversation_id: { type: 'string', required: true, pattern: CONV_ID, description: '私聊好友的 conversation_id。' },
        start_date: { type: 'string', format: 'date', description: '开始日期 YYYY-MM-DD（含），默认全部。' },
        end_date: { type: 'string', format: 'date', description: '结束日期 YYYY-MM-DD（含），默认全部。' },
      },
      check: args => (args.start_date && args.end_date && args.start_date > args.end_date ? 'start_date 不能晚于 end_date' : null),
      result: {
        run_id: true, conversation: true, period: { start: true, end: true },
        stats: 'json', material: { messages: { id: true, time: true, role: true, sender: true, text: true }, count: true, truncated: true },
        schema: 'json', instructions: true,
      },
    },
    {
      name: 'wechat_one_persona_render',
      action: 'persona_render',
      kind: 'write',
      idempotent: false,
      description: '校验人格报告语义稿（msg_id 必须来自本次素材、引文必须与原文逐字一致），通过后在本机生成 Markdown 与单文件 HTML 报告，返回文件路径。'
        + '校验失败时返回 errors 列表，请逐条修正后重试。',
      parameters: {
        run_id: { type: 'string', required: true, pattern: RUN_ID, description: 'persona_prepare 返回的 run_id。' },
        semantic: { type: 'json', required: true, description: '按 persona_prepare 返回的 schema 编写的语义稿对象。' },
        theme: { type: 'string', enum: ['newspaper', 'claude'], description: '报告主题，默认 newspaper。' },
      },
      approvalReason: () => '把通过证据校验的人格报告写入本机私有目录（Markdown 与 HTML 文件）。',
      result: { report_md: true, report_html: true, theme: true, sections: true, warnings: true },
    },
  ],
};
