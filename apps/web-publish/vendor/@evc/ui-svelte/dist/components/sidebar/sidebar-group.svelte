<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    children?: Snippet;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<div
  data-sidebar="group"
  class={cn('relative flex w-full min-w-0 flex-col p-2', className)}
  {...restProps}
>
  {@render children?.()}
</div>
