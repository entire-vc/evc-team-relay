<script lang="ts">
  import { Dialog as SheetPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';
  import { sheetVariants, type SheetVariants } from './variants.js';
  import SheetOverlay from './sheet-overlay.svelte';
  import SheetClose from './sheet-close.svelte';

  type Props = SheetPrimitive.ContentProps &
    SheetVariants & {
      class?: string;
    };

  let { class: className, side = 'right', children, ...restProps }: Props = $props();
</script>

<SheetPrimitive.Portal>
  <SheetOverlay />
  <SheetPrimitive.Content class={cn(sheetVariants({ side }), className)} {...restProps}>
    {@render children?.()}
    <SheetClose />
  </SheetPrimitive.Content>
</SheetPrimitive.Portal>
