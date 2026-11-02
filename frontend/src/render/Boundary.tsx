import { Component, type ReactNode } from "react";

/** Contains a render error to one block or card, so the rest of the page (and its Reject
 *  button) stays usable (gh #34). */
export class Boundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("codraft: a block failed to render", error);
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
