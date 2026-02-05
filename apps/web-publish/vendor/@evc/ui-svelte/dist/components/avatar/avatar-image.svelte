<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLImgAttributes } from 'svelte/elements';

  type Props = HTMLImgAttributes & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<img class={cn('aspect-square h-full w-full', className)} {...restProps} alt={restProps.alt ?? ''} />
