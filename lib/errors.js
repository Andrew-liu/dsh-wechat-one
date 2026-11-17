// 错误码目录（JS 侧）。引擎错误码会原样透传；这里补充 JS 层自身产生的错误。
export const JS_ERRORS = Object.freeze({
  INVALID_ARGS: { message: '参数不符合工具要求。', retryable: false },
  APPROVAL_REQUIRED: { message: '本次操作未获授权，没有访问任何聊天数据。', retryable: false },
  CANCELLED: { message: '操作已取消。', retryable: false },
  TIMEOUT: { message: '本地操作超时，原有索引保留。', retryable: true },
  OUTPUT_LIMIT: { message: '本地结果过大，请缩小查询范围。', retryable: false },
  PYTHON_UNAVAILABLE: { message: '需要 Python 3.10 或更高版本（含 SQLite FTS5）。可在插件配置 pythonExecutable 中指定。', retryable: false },
  PRIVATE_STATE_REQUIRED: { message: '本地数据目录必须在插件代码目录之外。', retryable: false },
  ENGINE_UNAVAILABLE: { message: '本地引擎没有返回有效结果。', retryable: true },
  ENGINE_ERROR: { message: '本地引擎内部错误，已记录日志。', retryable: false },
  SIDE_EFFECT_DENIED: { message: '该操作会产生外部副作用，当前策略不允许执行。', retryable: false },
  COMPONENT_UNAVAILABLE: { message: '该功能组件加载失败，其余功能不受影响。', retryable: false },
});

const CODE_RE = /^[A-Z][A-Z0-9_]{1,59}$/;

export function failure(code, message, extra = {}) {
  const known = JS_ERRORS[code];
  return {
    ok: false,
    error: {
      code,
      message: message || known?.message || '本地操作未完成。',
      retryable: extra.retryable ?? known?.retryable ?? false,
      ...(extra.details !== undefined ? { details: extra.details } : {}),
    },
  };
}

/** 规范化引擎返回的错误对象，丢弃不合规字段。 */
export function normalizeError(error) {
  const code = typeof error?.code === 'string' && CODE_RE.test(error.code) ? error.code : 'ENGINE_ERROR';
  const message = typeof error?.message === 'string' && error.message.length <= 500 ? error.message : undefined;
  const details = isPlainJson(error?.details) ? error.details : undefined;
  return failure(code, message, { retryable: error?.retryable === true, details });
}

function isPlainJson(value, depth = 0) {
  if (value === undefined || depth > 4) return false;
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.length <= 50 && value.every(v => isPlainJson(v, depth + 1));
  if (typeof value === 'object') return Object.values(value).every(v => isPlainJson(v, depth + 1));
  return false;
}
