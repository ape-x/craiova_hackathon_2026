import type { ComponentPropsWithRef, KeyboardEvent } from 'react';

export function commandDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
  if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
  event.preventDefault();
  event.currentTarget.form?.requestSubmit();
}

/** Keep multiline text intact; submitting still belongs to the ordinary form. */
export default function CommandDraft(
  props: Omit<ComponentPropsWithRef<'textarea'>, 'rows' | 'onKeyDown'>,
) {
  return <textarea {...props} rows={1} onKeyDown={commandDraftKeyDown} />;
}
