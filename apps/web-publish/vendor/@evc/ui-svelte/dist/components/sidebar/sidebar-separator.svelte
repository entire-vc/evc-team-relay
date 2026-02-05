<script lang="ts">
  import { cn } from '../../utils.js';
  import { Separator } from '../separator/index.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    orientation?: 'horizontal' | 'vertical';
    decorative?: boolean;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<Separator
  data-sidebar="separator"
  class={cn('mx-2 w-auto bg-sidebar-border', className)}
  {...restProps}
/>
