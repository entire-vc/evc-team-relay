<script lang="ts">
  import { cn } from '../../utils.js';
  import { alertVariants, type AlertVariants } from './variants.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> &
    AlertVariants & {
      class?: string;
    };

  let { class: className, variant = 'default', children, ...restProps }: Props = $props();
</script>

<div role="alert" class={cn(alertVariants({ variant }), className)} {...restProps}>
  {@render children?.()}
</div>
