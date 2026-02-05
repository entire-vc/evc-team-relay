<script lang="ts">
  import { cn } from '../../utils.js';
  import { Input } from '../input/index.js';
  import type { HTMLInputAttributes } from 'svelte/elements';

  type Props = HTMLInputAttributes & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<Input
  data-sidebar="input"
  class={cn(
    'h-8 w-full bg-background shadow-none focus-visible:ring-2 focus-visible:ring-sidebar-ring',
    className
  )}
  {...restProps}
/>
