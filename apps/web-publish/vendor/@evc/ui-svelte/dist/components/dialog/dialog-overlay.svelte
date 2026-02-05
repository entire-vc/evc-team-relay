<script lang="ts">
  import { Dialog as DialogPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = DialogPrimitive.OverlayProps & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<DialogPrimitive.Overlay
  class={cn(
    'fixed inset-0 z-50 bg-black/80 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
    className
  )}
  {...restProps}
/>
