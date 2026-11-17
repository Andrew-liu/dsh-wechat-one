// summary 组件：群聊 / 会话的日报、周报、月报。
const CONV_ID = '^conv_[0-9a-f]{16}$';
const RUN_ID = '^run_[0-9a-z_]{8,60}$';

const selection = {
  group_set: { type: 'string', maxLength: 40, description: '群集合名（插件配置 groupSets 或 ~/.wechat-report/config.yaml 的 group_sets）。' },
  chats: { type: 'array', maxItems: 20, items: { type: 'string', minLength: 1, maxLength: 80 },
    description: '群名或好友名（需精确；重名时会返回候选让用户确认）。' },
  conversation_ids: { type: 'array', maxItems: 20, items: { type: 'string', pattern: CONV_ID },
    description: 'wechat_one_contacts 返回的 conversation_id。' },
};
const SELECTION_RESULT = { label: true, conversations: { conversation_id: true, name: true } };
const WINDOW = { window: { period: true, label: true, start_date: true, end_date: true, range_text: true, slug: true } };
const MATERIAL_MESSAGE = { id: true, t: true, from: true, text: true, me: true, chat: true, url: true, mentions_me: true };

function needSelection(args) {
  if (!args.group_set && !args.chats?.length && !args.conversation_ids?.length) return '需要 group_set、chats 或 conversation_ids 之一';
  return null;
}

export default {
  id: 'summary',
  prompt: {
    name: 'tool:wechat-one-summary',
    order: 119,
    text: [
      '- 群聊日报 / 周报 / 月报：用 wechat_one_summary_prepare（period=daily 默认出昨天，weekly 上周，monthly 上月；date 指定某天所在周期），',
      '读完全部素材分块（parts_total>1 时用 wechat_one_summary_material 依次取），按 schema 写语义稿后调用 wechat_one_summary_render；',
      '校验失败按 errors 修正重试一次，仍失败用 stats_only=true 出统计版。warnings 提示数据过旧时先 wechat_one_refresh 再重出。',
      '- 定时日报：用户要求“每天几点出日报”时，用 schedule_create 建每日任务，指令写成：',
      '“调用 wechat_one_summary_pending（同样的群选择与 period）；有待出的期就先 wechat_one_refresh，再对每一期依次 prepare → render，',
      '最后简要汇报报告路径和一句话总结；没有待出的就不做任何事”。补跑最多 3 期。',
      '- 报告只存本机，不要把群成员原话整段贴到其他地方。',
    ].join(''),
  },
  tools: [
    {
      name: 'wechat_one_summary_pending',
      action: 'summary_pending',
      kind: 'read',
      description: '查询这组会话还有哪些报告期没出（日报默认最近一期为昨天；已出过时最多往前补 max_catchup 期）。不读取聊天正文，定时任务先调用它判断要不要出报告。',
      parameters: {
        ...selection,
        period: { type: 'string', enum: ['daily', 'weekly', 'monthly'], description: '周期，默认 daily。' },
        max_catchup: { type: 'integer', minimum: 1, maximum: 7, description: '最多补出几期，默认 3。' },
      },
      check: needSelection,
      result: {
        ...SELECTION_RESULT,
        pending: { period: true, label: true, start_date: true, end_date: true, range_text: true, slug: true },
        recent_done: true, coverage: { first_time: true, last_time: true, stale: true }, note: true,
      },
    },
    {
      name: 'wechat_one_summary_prepare',
      action: 'summary_prepare',
      kind: 'read',
      description: '为一组群聊/会话准备日报、周报或月报素材：本地计算统计（发言量、发言人、时段、每日热度、链接清单、@我的消息），'
        + '并把这期的文字消息（每条带 msg_id）按块返回第 1 块。返回 run_id、统计、素材分块数与语义稿 schema。',
      parameters: {
        ...selection,
        period: { type: 'string', enum: ['daily', 'weekly', 'monthly', 'custom'], description: '周期，默认 daily（昨天）。custom 需配 start_date/end_date。' },
        date: { type: 'string', format: 'date', description: '出该日期所在的那一期（日报即当天）。' },
        start_date: { type: 'string', format: 'date', description: 'custom 开始日期（含）。' },
        end_date: { type: 'string', format: 'date', description: 'custom 结束日期（含），最多 93 天。' },
      },
      check: args => needSelection(args)
        || (args.start_date && args.end_date && args.start_date > args.end_date ? 'start_date 不能晚于 end_date' : null),
      result: {
        run_id: true, ...SELECTION_RESULT, ...WINDOW, warnings: true, stats: 'json', mentions_me: true,
        material: { part: true, parts_total: true, count: true, messages: MATERIAL_MESSAGE },
        schema: 'json', instructions: true,
      },
    },
    {
      name: 'wechat_one_summary_material',
      action: 'summary_material',
      kind: 'read',
      description: '读取 summary_prepare 素材的第 N 块（第 1 块已随 prepare 返回）。',
      parameters: {
        run_id: { type: 'string', required: true, pattern: RUN_ID, description: 'summary_prepare 返回的 run_id。' },
        part: { type: 'integer', required: true, minimum: 1, maximum: 10, description: '分块序号，从 1 开始。' },
      },
      result: { run_id: true, part: true, parts_total: true, messages: MATERIAL_MESSAGE },
    },
    {
      name: 'wechat_one_summary_render',
      action: 'summary_render',
      kind: 'write',
      idempotent: false,
      description: '校验日报语义稿（msg_id 必须来自本期素材、引文逐字一致、会话归属、链接与日期在范围内），通过后在本机生成 Markdown 与单文件 HTML 报告，'
        + '并记录这一期已出（供补跑判断）。stats_only=true 时跳过语义稿，只出统计版。',
      parameters: {
        run_id: { type: 'string', required: true, pattern: RUN_ID, description: 'summary_prepare 返回的 run_id。' },
        semantic: { type: 'json', description: '按 summary_prepare 返回的 schema 编写的语义稿对象。' },
        stats_only: { type: 'boolean', description: '只出统计版（语义稿两次校验失败时的兜底）。' },
        theme: { type: 'string', enum: ['claude', 'newspaper'], description: '报告主题，默认 claude。' },
      },
      check: args => (args.semantic === undefined && args.stats_only !== true ? '需要 semantic，或 stats_only=true' : null),
      approvalReason: () => '把这期聊天报告（Markdown 与 HTML）写入本机私有目录。',
      result: {
        report_md: true, report_html: true, theme: true, stats_only: true, label: true, recorded: true, ...WINDOW,
        sections: { key: true, label: true, count: true }, warnings: true,
      },
    },
  ],
};
