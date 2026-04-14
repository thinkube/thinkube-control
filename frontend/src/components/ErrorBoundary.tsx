import React, { Component, ErrorInfo, ReactNode } from 'react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkCodeBlock } from 'thinkube-style/components/feedback';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('🔴 ERROR BOUNDARY CAUGHT:', {
      error,
      errorInfo,
      componentStack: errorInfo.componentStack
    });
    this.setState({ error, errorInfo });
  }

  public render() {
    if (this.state.hasError) {
      return (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Something went wrong</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <details>
              <summary className="cursor-pointer font-semibold mb-2">Error details</summary>
              <div className="space-y-2">
                <div>
                  <strong>Error:</strong> {this.state.error?.toString()}
                </div>
                {this.state.error?.stack && (
                  <div>
                    <strong>Stack:</strong>
                    <TkCodeBlock>{this.state.error.stack}</TkCodeBlock>
                  </div>
                )}
                {this.state.errorInfo?.componentStack && (
                  <div>
                    <strong>Component Stack:</strong>
                    <TkCodeBlock>{this.state.errorInfo.componentStack}</TkCodeBlock>
                  </div>
                )}
              </div>
            </details>
          </TkCardContent>
        </TkCard>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
