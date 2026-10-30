import { Component, type ErrorInfo } from 'react';
import type { CopyNode } from '../copy/text.ts';

export interface RegionBoundaryProps {
  /** What to draw instead of a part that threw while rendering (it keeps its revocations, S-091). */
  readonly fallback: CopyNode;
  /** A change of this key (the route or the context epoch) clears the failure and draws the part again. */
  readonly resetKey?: string;
  /** Told once per failure (one diagnostic per distinct fault, S-090). */
  readonly onError?: (error: unknown, info: ErrorInfo) => void;
  readonly children: CopyNode;
}

interface RegionBoundaryState {
  readonly failed: boolean;
}

/**
 * Keeps a rendering failure inside one part of the page (S-090). The fallback
 * is drawn once and then stays, updating in place as the store changes, so a
 * poll with the same fault never redraws it and focus stays where it was
 * (S-094); only a change of `resetKey` tries the part again.
 */
export class RegionBoundary extends Component<RegionBoundaryProps, RegionBoundaryState> {
  override state: RegionBoundaryState = { failed: false };

  static getDerivedStateFromError(): RegionBoundaryState {
    return { failed: true };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  override componentDidUpdate(previous: RegionBoundaryProps): void {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  override render(): CopyNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
