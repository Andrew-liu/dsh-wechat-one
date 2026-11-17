// read 组件：会话查找、精确检索、主题检索（RAG）、上下文。
const CONV_ID = '^conv_[0-9a-f]{16}$';
const MSG_ID = '^m[0-9a-f]{16}$';

const MESSAGE = { message_id: true, time: true, sender: true, is_self: true, type: true, text: true, truncated: true };
const MESSAGE_WITH_CONV = { ...MESSAGE, conversation_id: true, conversation: true, kind: true };
const COVERAGE = { coverage: { first_time: true, last_time: true, stale: true } };
const PAGE = { total: true, returned: true, has_more: true, next_cursor: true, note: true };

const dateRange = {
  start_date: { type: 'string', format: 'date', description: '开始日期 YYYY-MM-DD（含）。' },
  end_date: { type: 'string', format: 'date', description: '结束日期 YYYY-MM-DD（含当天）。' },
};
const conversationIds = {
  type: 'array', maxItems: 20, items: { type: 'string', pattern: CONV_ID },
  description: '只在这些会话内查找（先用 wechat_one_contacts 获取 conversation_id）。',
};
const cursor = { type: 'string', maxLength: 200, description: '上一页返回的 next_cursor；不要自行拼接。' };

function checkDates(args) {
  if (args.start_date && args.end_date && args.start_date > args.end_date) return 'start_date 不能晚于 end_date';
  return null;
}

export default {
  id: 'read',
  prompt: {
    name: 'tool:wechat-one-read',
    order: 117,
    text: [
      '- 用户想找“聊过的某类话题/技巧/经验”（如“涨粉技巧”）时用 wechat_one_topic_search：先自行把主题扩展成 5~20 个检索词',
      '（同义词、口语说法、圈内黑话、英文/缩写，如 涨粉→起号、粉丝增长、流量、爆款、推荐算法、完播率、互动率、引流、关注），',
      '再按“要点/技巧”归纳结果：每个要点给出原话摘录、发言人、会话名、日期和 msg_id，把建议、反对意见、亲测结果分开写；',
      '命中太少时换一组检索词补检（最多两轮），仍不足就如实说明。需要更多前后文时用 wechat_one_context。',
      '- 用户要精确的关键词、某人某天说过的话时用 wechat_one_search；指定好友或群时先用 wechat_one_contacts 找到 conversation_id，重名时让用户确认。',
    ].join(''),
  },
  tools: [
    {
      name: 'wechat_one_contacts',
      action: 'contacts',
      kind: 'read',
      description: '按名称查找本地索引中的会话（好友 / 群聊），返回显示名、conversation_id、消息数与最近时间。不返回聊天正文。名称重名时必须让用户确认。',
      parameters: {
        query: { type: 'string', maxLength: 50, description: '名称关键词（备注、昵称或群名的一部分）；留空按最近活跃排序。' },
        kind: { type: 'string', enum: ['friend', 'group'], description: '只看好友或只看群聊。' },
        limit: { type: 'integer', minimum: 1, maximum: 50, description: '每页条数，默认 20。' },
        cursor,
      },
      result: { conversations: { conversation_id: true, name: true, kind: true, message_count: true, last_time: true }, ...PAGE },
    },
    {
      name: 'wechat_one_search',
      action: 'search',
      kind: 'read',
      description: '精确关键词检索消息（中文两字词即可命中），可按会话、发言人、日期过滤，按时间倒序分页返回消息原文。',
      parameters: {
        query: { type: 'string', required: true, minLength: 1, maxLength: 100, description: '关键词；空格分隔表示同时包含。' },
        conversation_ids: conversationIds,
        sender: { type: 'string', maxLength: 50, description: '只看该发言人（显示名的一部分）。' },
        ...dateRange,
        limit: { type: 'integer', minimum: 1, maximum: 100, description: '每页条数，默认取插件配置 maxItems。' },
        cursor,
      },
      check: checkDates,
      result: { messages: MESSAGE_WITH_CONV, ...PAGE, ...COVERAGE },
    },
    {
      name: 'wechat_one_topic_search',
      action: 'topic_search',
      kind: 'read',
      description: '主题检索（RAG）：用主题词和你扩展出的检索词，在所有会话里召回相关的对话片段（按 30 分钟间隔切分），'
        + '按相关度排序并保证来源多样（同一会话每页最多 3 段）。返回片段内的消息原文，供你归纳成带出处的要点。',
      parameters: {
        topic: { type: 'string', required: true, minLength: 1, maxLength: 50, description: '用户关心的主题原词，如“涨粉技巧”。' },
        keywords: { type: 'array', required: true, minItems: 1, maxItems: 20,
          items: { type: 'string', minLength: 1, maxLength: 30 }, description: '扩展检索词 5~20 个：同义词、口语、黑话、英文缩写等。' },
        conversation_ids: conversationIds,
        kinds: { type: 'array', maxItems: 2, items: { type: 'string', enum: ['friend', 'group'] }, description: '只在好友或群聊中检索。' },
        ...dateRange,
        limit: { type: 'integer', minimum: 1, maximum: 20, description: '每页片段数，默认 10。' },
        cursor,
      },
      check: checkDates,
      result: {
        chunks: {
          chunk_id: true, conversation_id: true, conversation: true, kind: true, start_time: true, end_time: true,
          matched_terms: true, score: true, messages: MESSAGE,
        },
        retrieval: { mode: true, vector: true, terms_without_hits: true },
        ...PAGE, ...COVERAGE,
      },
    },
    {
      name: 'wechat_one_context',
      action: 'context',
      kind: 'read',
      description: '读取某条消息在同一会话中的前后文（默认前后各 5 条，最多各 20 条），用于核对原意、承诺与结论。',
      parameters: {
        message_id: { type: 'string', required: true, pattern: MSG_ID, description: '检索结果中的 message_id。' },
        before: { type: 'integer', minimum: 0, maximum: 20, description: '前文条数，默认 5。' },
        after: { type: 'integer', minimum: 0, maximum: 20, description: '后文条数，默认 5。' },
      },
      result: {
        conversation_id: true, conversation: true, kind: true, target: true,
        messages: MESSAGE, has_more_before: true, has_more_after: true,
      },
    },
  ],
};
