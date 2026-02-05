<script lang="ts">
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLElement> & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<nav aria-label="breadcrumb" {...restProps}>
  {@render children?.()}
</nav>
