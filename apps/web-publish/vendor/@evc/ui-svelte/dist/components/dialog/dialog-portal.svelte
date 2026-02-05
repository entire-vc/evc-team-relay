<script lang="ts">
  import { Dialog as DialogPrimitive } from 'bits-ui';

  type Props = DialogPrimitive.PortalProps;

  let { children, ...restProps }: Props = $props();
</script>

<DialogPrimitive.Portal {...restProps}>
  {@render children?.()}
</DialogPrimitive.Portal>
