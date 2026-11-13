function handler(input) {
  let result = input;
  if (typeof result === 'string') result = JSON.parse(result);
  if (result && result.body !== undefined) {
    result = typeof result.body === 'string' ? JSON.parse(result.body) : result.body;
  }
  if (!result || result.accepted !== true || !result.key) {
    throw new Error('Event was not durably accepted');
  }
  return { accepted: true, key: result.key, status: result.status };
}
