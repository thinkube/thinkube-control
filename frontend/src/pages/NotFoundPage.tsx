import { Link } from 'react-router-dom'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardContent, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data'

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-8"> {/* @allowed-inline */}
      <TkCard className="w-full max-w-md">
        <TkCardHeader className="items-center text-center">
          <div className="mb-6">
            <span className="text-9xl font-bold text-primary">404</span>
          </div>
          <TkCardTitle className="text-2xl">Page Not Found</TkCardTitle>
        </TkCardHeader>
        <TkCardContent className="space-y-6 text-center">
          <p className="text-muted-foreground">
            The page you're looking for doesn't exist or has been moved.
          </p>
          <Link to="/">
            <TkButton variant="default" className="w-full">
              Back to Dashboard
            </TkButton>
          </Link>
        </TkCardContent>
      </TkCard>
    </div>
  )
}
