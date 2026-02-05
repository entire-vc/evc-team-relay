<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<div class={cn('animate-pulse rounded-md bg-primary/10', className)} {...restProps}></div>
