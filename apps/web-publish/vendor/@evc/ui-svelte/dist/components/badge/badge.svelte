<script lang="ts">
  import { cn } from '../../utils.js';
  import { badgeVariants, type BadgeVariants } from './variants.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> &
    BadgeVariants & {
      class?: string;
    };

  let { class: className, variant = 'default', children, ...restProps }: Props = $props();
</script>

<div class={cn(badgeVariants({ variant }), className)} {...restProps}>
  {@render children?.()}
</div>
