<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<nav
  aria-label="pagination"
  class={cn('mx-auto flex w-full justify-center', className)}
  {...restProps}
>
  {@render children?.()}
</nav>
