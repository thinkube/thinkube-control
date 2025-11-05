'use client';

import { TkButton } from 'thinkube-style/components/buttons-badges';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html>
      <body>
        <div style={{ padding: '2rem', textAlign: 'center' }}>
          <h1>Something went wrong!</h1>
          <p>{error.message}</p>
          <TkButton onClick={() => reset()}>Try again</TkButton>
        </div>
      </body>
    </html>
  );
}
