import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import PlatformResultsDialog from './PlatformResultsDialog';

describe('platform action evidence', () => {
  it('shows provider records, missing inputs and failures without exposing bearer fields or interpreting HTML', () => {
    const html = renderToStaticMarkup(
      <PlatformResultsDialog
        onClose={() => {}}
        results={[
          {
            name: 'travel.search',
            status: 'succeeded',
            result: {
              provider: 'Amadeus',
              offers: [
                {
                  name: '<script>alert(1)</script>',
                  price: 123,
                  currency: 'EUR',
                  token: 'bearer-secret',
                },
              ],
              world: { groups: [{ token: 'world-secret' }] },
              api_key: 'api-secret',
            },
          },
          { name: 'recovery.start', status: 'needs_input', missing_fields: ['group_ids'] },
          { name: 'hotels.search', status: 'failed', error: 'Supplier timeout' },
        ]}
      />,
    );
    expect(html).toContain('Amadeus');
    expect(html).toContain('123');
    expect(html).toContain('EUR');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('bearer-secret');
    expect(html).not.toContain('world-secret');
    expect(html).not.toContain('api-secret');
    expect(html).toContain('Provide: group ids');
    expect(html).toContain('Supplier timeout');
  });
});
