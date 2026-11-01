import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { KeyboardEvent } from 'react';
import CommandDraft, { commandDraftKeyDown } from './CommandDraft';

function eventFor(key: string, shiftKey = false, isComposing = false) {
  const submit = vi.fn();
  const preventDefault = vi.fn();
  const event = {
    key,
    shiftKey,
    nativeEvent: { isComposing },
    currentTarget: { form: { requestSubmit: submit } },
    preventDefault,
  } as unknown as KeyboardEvent<HTMLTextAreaElement>;
  return { event, submit, preventDefault };
}

describe('ordinary multiline command draft', () => {
  it('renders exact copied multiline text without sending it or replacing the draft', () => {
    const text = '  Aeroportul Londra Heathrow.\nPăstrează G1 împreună.  ';
    const change = vi.fn();
    const html = renderToStaticMarkup(
      <CommandDraft id="scenario-command" value={text} onChange={change} maxLength={4000} />,
    );
    expect(html).toContain('<textarea');
    expect(html).toContain('rows="1"');
    expect(html).toContain(text);
    expect(html).toContain('id="scenario-command"');
    expect(change).not.toHaveBeenCalled();
  });

  it('sends through the existing form only on a plain Enter key', () => {
    const { event, submit, preventDefault } = eventFor('Enter');
    commandDraftKeyDown(event);
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(submit).toHaveBeenCalledOnce();
  });

  it.each([
    ['Enter', true, false],
    ['Enter', false, true],
    ['Enter', true, true],
    ['a', false, false],
  ] as const)(
    'does not submit or suppress native editing for key=%s shift=%s composing=%s',
    (key, shift, composing) => {
      const { event, submit, preventDefault } = eventFor(key, shift, composing);
      commandDraftKeyDown(event);
      expect(submit).not.toHaveBeenCalled();
      expect(preventDefault).not.toHaveBeenCalled();
    },
  );
});
