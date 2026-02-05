<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLAttributes<HTMLUListElement> & {
    class?: string;
    children?: Snippet;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<ul
  data-sidebar="menu"
  class={cn('flex w-full min-w-0 flex-col gap-1', className)}
  {...restProps}
>
  {@render children?.()}
</ul>
