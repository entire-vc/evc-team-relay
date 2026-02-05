<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLSpanElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<span
  role="link"
  aria-disabled="true"
  aria-current="page"
  class={cn('font-normal text-foreground', className)}
  {...restProps}
>
  {@render children?.()}
</span>
