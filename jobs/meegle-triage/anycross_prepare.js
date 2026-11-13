function handler(input) {
  const id = String(input.id || '').trim();
  const event = input.event;
  const issue = event.issue;
  if (!/^[1-9][0-9]*$/.test(id)) {
    throw new Error('Missing confirmed source item');
  }
  const payload = {
    repository: event.repository,
    number: Number(issue.number),
    source_id: id,
    github_url: issue.html_url,
  };
  return {
    id,
    event_body: JSON.stringify(payload),
    uuid: event.repository.replace('/', '-') + '-' + issue.number,
  };
}
