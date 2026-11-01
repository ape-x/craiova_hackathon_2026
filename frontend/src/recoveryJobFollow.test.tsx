import { describe, expect, it, vi } from 'vitest';
import { revealRecoveryJob } from './RecoveryJobPanel';
import { recoveryJobAnchor } from './recoveryJobs';
import type { RecoveryJob } from './types';

const follow = {
  job: { id: 'JOB/1', incident_id: 'CASE1' } as RecoveryJob,
  request: 3,
};

function renderedArticle(reduced = false) {
  const history = { open: false };
  const article = {
    closest: vi.fn(() => history),
    scrollIntoView: vi.fn(),
    focus: vi.fn(),
  };
  const document = {
    getElementById: vi.fn(() => article as unknown as HTMLElement | null),
    defaultView: { matchMedia: vi.fn(() => ({ matches: reduced })) },
  };
  const contains = vi.fn(() => true);
  const panel = { ownerDocument: document, contains } as unknown as HTMLElement;
  return { history, article, document, contains, panel };
}

describe('follow the acknowledged recovery attempt', () => {
  it('waits for the article, then reveals its collapsed history and focuses without a second scroll', () => {
    const { panel, document, article, history } = renderedArticle();
    document.getElementById.mockReturnValueOnce(null);
    const waiting = revealRecoveryJob(follow, 'CASE1', null, panel);
    expect(waiting).toBeNull();
    expect(article.scrollIntoView).not.toHaveBeenCalled();
    const completed = revealRecoveryJob(follow, 'CASE1', waiting, panel);
    expect(completed).not.toBeNull();
    expect(document.getElementById).toHaveBeenLastCalledWith(recoveryJobAnchor('JOB/1'));
    expect(history.open).toBe(true);
    expect(article.scrollIntoView).toHaveBeenCalledExactlyOnceWith({
      behavior: 'smooth',
      block: 'center',
    });
    expect(article.focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true });
  });

  it('does not recenter on polls or effect replays, but does for another voice request on the same job', () => {
    const { panel, article } = renderedArticle();
    const completed = revealRecoveryJob(follow, 'CASE1', null, panel);
    const updated = { ...follow, job: { ...follow.job, status: 'completed' as const } };
    expect(revealRecoveryJob(updated, 'CASE1', completed, panel)).toBe(completed);
    expect(article.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(revealRecoveryJob({ ...updated, request: 4 }, 'CASE1', completed, panel)).not.toBe(
      completed,
    );
    expect(article.scrollIntoView).toHaveBeenCalledTimes(2);
  });

  it('includes the job ID in the focus identity', () => {
    const { panel, article } = renderedArticle();
    const completed = revealRecoveryJob(follow, 'CASE1', null, panel);
    const other = { ...follow, job: { ...follow.job, id: 'JOB2' } };
    expect(revealRecoveryJob(other, 'CASE1', completed, panel)).not.toBe(completed);
    expect(article.scrollIntoView).toHaveBeenCalledTimes(2);
  });

  it('does not navigate to an attempt from another case or another rendered panel', () => {
    const { panel, document, contains, article } = renderedArticle();
    expect(revealRecoveryJob(follow, 'CASE2', null, panel)).toBeNull();
    expect(document.getElementById).not.toHaveBeenCalled();
    contains.mockReturnValue(false);
    expect(revealRecoveryJob(follow, 'CASE1', null, panel)).toBeNull();
    expect(article.scrollIntoView).not.toHaveBeenCalled();
    expect(article.focus).not.toHaveBeenCalled();
  });

  it('respects reduced motion while still revealing and focusing the result', () => {
    const { panel, document, article, history } = renderedArticle(true);
    revealRecoveryJob(follow, 'CASE1', null, panel);
    expect(document.defaultView.matchMedia).toHaveBeenCalledWith(
      '(prefers-reduced-motion: reduce)',
    );
    expect(article.scrollIntoView).toHaveBeenCalledExactlyOnceWith({
      behavior: 'auto',
      block: 'center',
    });
    expect(article.focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true });
    expect(history.open).toBe(true);
  });

  it('leaves previous navigation intact when no follow request or panel is present', () => {
    const { panel, document } = renderedArticle();
    expect(revealRecoveryJob(undefined, 'CASE1', 'previous', panel)).toBe('previous');
    expect(revealRecoveryJob(follow, 'CASE1', 'previous', null)).toBe('previous');
    expect(document.getElementById).not.toHaveBeenCalled();
  });
});
